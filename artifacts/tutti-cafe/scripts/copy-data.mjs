import path from 'node:path';
import { cp, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const artifactDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sourceDir = path.join(artifactDir, 'data');
const targetDir = path.join(artifactDir, 'dist', 'public', 'data');

await mkdir(targetDir, { recursive: true });
await cp(sourceDir, targetDir, { recursive: true, force: true });