// Node.js 18+; VISION_INSPECTION_TOKEN must match the independent service.
// Usage: node examples/inspection-service-client.mjs ./image.png http://127.0.0.1:8765
import { readFile } from 'node:fs/promises';
const [imagePath, baseURL = 'http://127.0.0.1:8765'] = process.argv.slice(2);
const token = process.env.VISION_INSPECTION_TOKEN;
if (!imagePath || !token) throw new Error('Image path and VISION_INSPECTION_TOKEN are required');
const headers = { 'X-Vision-Token': token };
const queued = await fetch(`${baseURL}/v1/jobs/upload`, { method: 'POST', headers: { ...headers, 'Content-Type': 'application/octet-stream' }, body: await readFile(imagePath) });
if (!queued.ok) throw new Error(await queued.text());
const { job_id: jobID } = await queued.json();
let result;
for (let count = 0; count < 300; count += 1) {
  const response = await fetch(`${baseURL}/v1/jobs/${encodeURIComponent(jobID)}`, { headers });
  if (!response.ok) throw new Error(await response.text());
  result = await response.json();
  if (!['queued', 'running', 'delivery_pending'].includes(result.state)) break;
  await new Promise(resolve => setTimeout(resolve, 200));
}
if (['queued', 'running', 'delivery_pending'].includes(result?.state)) throw new Error('Inspection/field acknowledgment timed out; do not treat this as OK');
console.log(JSON.stringify(result, null, 2));
if (result.verdict === 'REVIEW') process.exitCode = 2;
