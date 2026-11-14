// Offline check: node third_party/label-studio/verify-provenance.cjs
const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const {existsSync, readFileSync} = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const manifest = JSON.parse(readFileSync(path.join(__dirname, 'provenance.json'), 'utf8'));
const hash = value => createHash('sha256').update(value).digest('hex');
const read = file => readFileSync(path.join(root, file));
for (const file of manifest.files) {
  assert.equal(hash(read(file.local_path)), file.sha256, file.local_path);
}
for (const name of ['LICENSE', 'NOTICE']) {
  const distributed = `public/third-party/label-studio/${name}`;
  assert.ok(existsSync(path.join(root, distributed)), `distributed ${name} is present`);
  assert.deepEqual(read(distributed), read(`third_party/label-studio/${name}`), `distributed ${name} keeps exact upstream bytes`);
}
const source = read('third_party/label-studio/upstream/PolygonRegion.jsx').toString('utf8');
const start = source.indexOf('function getAnchorPoint(');
const end = source.indexOf('\nfunction getFlattenedPoints(', start);
assert.ok(start >= 0 && end > start, 'pinned function is present');
const extracted = source.slice(start, end).trimEnd() + '\n';
assert.equal(hash(extracted), manifest.extracted_function_sha256, 'original extracted function');
const body = extracted.slice(extracted.indexOf(' {\n') + 3);
assert.equal(hash(body), manifest.arithmetic_body_sha256, 'original arithmetic body');
const typed = read(manifest.typed_module);
assert.equal(hash(typed), manifest.typed_module_sha256, 'typed module');
const typedSource = typed.toString('utf8');
const typedSignatureEnd = '}): [number, number] {\n';
const signatureEnd = typedSource.indexOf(typedSignatureEnd);
assert.ok(signatureEnd >= 0, 'typed signature adaptation is present');
assert.equal(typedSource.slice(signatureEnd + typedSignatureEnd.length), body, 'unchanged arithmetic body');
console.log('Verified 3 pinned source/license/NOTICE hashes, exact distributed LICENSE/NOTICE bytes, original function, typed module and unchanged arithmetic body.');
