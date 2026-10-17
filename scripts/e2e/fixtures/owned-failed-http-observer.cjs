'use strict';
// Passive original Page event observation only. Never route, fetch, evaluate,
// focus, screenshot, change settings, or renew any existing fixture deadline.
const crypto = require('node:crypto');
const MAX_BODY_BYTES = 1024 * 1024;
const MAX_FAILED_RESPONSES = 16;
const MAX_CONSOLE_DETAILS = 64;
function observeOwnedFailedHTTP(page, {isAllowedPort}) {
  if (typeof isAllowedPort !== 'function') throw Error('Original allowed-port predicate required');
  const failed = [], consoleDetails = [], errors = [], pending = new Set();
  let sequence = 0, stopped = false;
  const ownedURL = value => {
    let url; try { url = new URL(value); } catch { return false; }
    if (!['http:', 'https:'].includes(url.protocol) || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) return false;
    return isAllowedPort(Number(url.port || (url.protocol === 'https:' ? 443 : 80)));
  };
  const onConsole = message => {
    if (message.type() !== 'error') return;
    if (consoleDetails.length >= MAX_CONSOLE_DETAILS) { errors.push({operation:'console_location_limit', epoch:Date.now()}); return; }
    // This supplements the original unfiltered consoleErrors text array.
    consoleDetails.push({epoch:Date.now(), text:message.text(), location:message.location()});
  };
  const onResponse = response => {
    if (response.status() < 400 || !ownedURL(response.url())) return;
    let request;
    try { request = response.request(); if (request.frame() !== page.mainFrame()) return; }
    catch (error) { errors.push({operation:'original_response_owner',url:response.url(),error:String(error)}); return; }
    if (++sequence > MAX_FAILED_RESPONSES) { errors.push({operation:'failed_response_limit',url:response.url(),status:response.status(),epoch:Date.now()}); return; }
    const record = {sequence,epoch:Date.now(),url:response.url(),request_url:request.url(),method:request.method(),resource_type:request.resourceType(),status:response.status(),main_frame:true,response_fabricated:false,full_original_body:false};
    failed.push(record);
    const promise = (async () => {
      const declared = response.headers()['content-length'];
      if (declared && /^\d+$/.test(declared) && Number(declared) > MAX_BODY_BYTES) throw Error('Original failed body exceeds existing 1MiB diagnostic limit');
      const body = await response.body();
      if (body.length > MAX_BODY_BYTES) throw Error('Original failed body exceeds existing 1MiB diagnostic limit');
      const finish = await response.finished();
      if (finish !== null) throw Error('Original response did not finish: ' + String(finish));
      Object.assign(record,{body_bytes:body.length,body_sha256:crypto.createHash('sha256').update(body).digest('hex'),body_base64:body.toString('base64'),finished:true,full_original_body:true});
    })().catch(error => { record.capture_error = String(error); errors.push({operation:'original_failed_response_body',sequence:record.sequence,url:record.url,error:String(error)}); });
    pending.add(promise); void promise.finally(() => pending.delete(promise));
  };
  page.on('response', onResponse); page.on('console', onConsole);
  return {
    async finishAfterOriginalContextClose(originalContextClosed) {
      if (stopped) throw Error('Original observer can be settled only once');
      stopped = true; page.off('response',onResponse); page.off('console',onConsole);
      // Original context close settles original body reads. No timer, new
      // network request, context close, cancellation or deadline is introduced.
      if (originalContextClosed === true) await Promise.allSettled([...pending]);
      return {schema:'modu-vision.owned-page-failed-http-observation/v1',failed_responses:failed,console_error_locations:consoleDetails,observer_errors:errors,pending_count:pending.size,original_context_closed:originalContextClosed===true,passive_original_events:true,original_deadlines_unchanged:true,network_requests_created:0,route_or_response_mutation:false,UI_actions:0,historical_Root07_URL_reconstructed:false};
    },
  };
}
module.exports = {observeOwnedFailedHTTP, MAX_BODY_BYTES, MAX_FAILED_RESPONSES};
