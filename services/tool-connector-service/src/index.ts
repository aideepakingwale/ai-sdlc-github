import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import Fastify from 'fastify';
import { Redis } from 'ioredis';
import { pino } from 'pino';
import type { z } from 'zod';
import { isSdlcError, loadEnv, TOOL_REGISTRY, ToolsEnvSchema } from '@sdlc/shared';
import { createToolRuntime } from './registry.js';

const env = loadEnv(ToolsEnvSchema);
const log = pino({ level: env.LOG_LEVEL, name: 'tool-connector' });

const redis = new Redis(env.REDIS_URL, { maxRetriesPerRequest: 2 });
const runtime = createToolRuntime(env, redis, log);
log.info({ modes: runtime.modes }, 'tool connector modes resolved');

/**
 * Stateless MCP over Streamable HTTP: a fresh McpServer + transport pair
 * per request avoids cross-client request-id collisions; tool handlers close
 * over the shared runtime (validation + cache + connectors).
 */
function buildMcpServer(): McpServer {
  const server = new McpServer({ name: 'sdlc-tool-connector', version: '0.1.0' });
  for (const def of Object.values(TOOL_REGISTRY)) {
    const inputShape = (def.input as unknown as z.AnyZodObject).shape as z.ZodRawShape;
    server.registerTool(
      def.name,
      {
        description: def.persona ? `${def.description} [LLM persona]` : def.description,
        inputSchema: inputShape,
      },
      async (args: Record<string, unknown>) => {
        try {
          const result = await runtime.execute(def.name, args);
          return { content: [{ type: 'text' as const, text: JSON.stringify(result) }] };
        } catch (err) {
          const message = isSdlcError(err) ? `${err.code}: ${err.message}` : 'TOOL_ERROR: internal failure';
          return { content: [{ type: 'text' as const, text: message }], isError: true };
        }
      },
    );
  }
  return server;
}

const app = Fastify({ logger: false, bodyLimit: 16 * 1024 * 1024 });

app.get('/healthz', async () => ({ status: 'ok' }));

app.get('/readyz', async (_req, reply) => {
  try {
    await redis.ping();
    return { status: 'ok', modes: runtime.modes };
  } catch {
    return reply.status(503).send({ status: 'degraded', redis: 'down' });
  }
});

app.post('/mcp', async (req, reply) => {
  const server = buildMcpServer();
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: undefined,
    enableJsonResponse: true,
  });
  reply.hijack();
  // Cleanup keys off the RESPONSE stream: req.raw 'close' fires as soon as the
  // request body is consumed (Node 18+), which would kill the transport before
  // the JSON-RPC response is written.
  reply.raw.on('close', () => {
    void transport.close();
    void server.close();
  });
  try {
    await server.connect(transport);
    await transport.handleRequest(req.raw, reply.raw, req.body);
  } catch (err) {
    log.error({ err }, 'mcp request failed');
    if (!reply.raw.headersSent) {
      reply.raw.writeHead(500, { 'content-type': 'application/json' });
      reply.raw.end(JSON.stringify({ jsonrpc: '2.0', error: { code: -32603, message: 'Internal error' }, id: null }));
    }
  }
});

// Stateless server: session-oriented verbs are not supported.
app.get('/mcp', async (_req, reply) => reply.status(405).send({ error: 'stateless MCP: POST only' }));
app.delete('/mcp', async (_req, reply) => reply.status(405).send({ error: 'stateless MCP: POST only' }));

async function main() {
  await app.listen({ port: env.TOOLS_PORT, host: '0.0.0.0' });
  log.info({ port: env.TOOLS_PORT }, 'tool-connector-service listening (MCP at /mcp)');
}

async function shutdown(signal: string) {
  log.info({ signal }, 'shutting down');
  await app.close();
  redis.disconnect();
  process.exit(0);
}

process.on('SIGTERM', () => void shutdown('SIGTERM'));
process.on('SIGINT', () => void shutdown('SIGINT'));

main().catch((err) => {
  log.error({ err }, 'fatal boot error');
  process.exit(1);
});
