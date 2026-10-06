import { describe, expect, it } from 'vitest';
import { allDirs, countFiles, filterTree, iconFor, langForPath, steps, type CodeNode, type CodeView } from './codeTree';

const f = (path: string, purpose = '', generated = false): CodeNode => ({ name: path.split('/').pop()!, path, type: 'file', purpose, kind: 'source', generated });
const tree: CodeNode = { name: '', path: '', type: 'dir', purpose: '', children: [
  { name: 'src', path: 'src', type: 'dir', purpose: 'source', children: [
    { name: 'api', path: 'src/api', type: 'dir', purpose: '', children: [f('src/api/routes.py', 'HTTP endpoints', true)] },
    f('src/main.py', 'entry point', true)] },
  { name: 'tests', path: 'tests', type: 'dir', purpose: '', children: [f('tests/test_main.py', 'tests for main')] },
  f('README.md', 'how to run')] };

describe('code explorer model', () => {
  it('walks the journey: structure proposed, approved, code written, committed', () => {
    const at = (status: CodeView['status']) => steps({ enabled: true, status }).map((s) => s.state);
    expect(at('none')).toEqual(['current', 'todo', 'todo', 'todo']);
    expect(at('proposed')).toEqual(['done', 'current', 'todo', 'todo']);
    expect(at('approved')).toEqual(['done', 'done', 'current', 'todo']);
    expect(at('implemented')).toEqual(['done', 'done', 'done', 'current']);
    expect(at('committed')).toEqual(['done', 'done', 'done', 'done']);
  });

  it('lists every directory and counts files with how many are written', () => {
    expect(allDirs(tree)).toEqual(['src', 'src/api', 'tests']);
    expect(countFiles(tree)).toEqual({ files: 4, generated: 2 });
  });

  it('filters by path or purpose and keeps the folders leading to a match', () => {
    const byPath = filterTree(tree, 'routes')!;
    expect(allDirs(byPath)).toEqual(['src', 'src/api']);
    expect(countFiles(byPath).files).toBe(1);
    const byPurpose = filterTree(tree, 'how to run')!;
    expect(byPurpose.children!.map((c) => c.path)).toEqual(['README.md']);
    expect(filterTree(tree, 'zzz')!.children).toEqual([]);
    expect(filterTree(tree, '  ')).toBe(tree);
  });

  it('picks a language and icon from the file name', () => {
    expect(langForPath('src/app/main.py')).toBe('python');
    expect(langForPath('Dockerfile')).toBe('dockerfile');
    expect(langForPath('infra/Dockerfile.prod')).toBe('dockerfile');
    expect(langForPath('pyproject.toml')).toBe('ini');
    expect(langForPath('LICENSE')).toBe('plaintext');
    expect(iconFor('a.ts')).toBe('🟦');
  });
});
