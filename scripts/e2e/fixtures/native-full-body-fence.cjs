/* Test-only opt-in for complete fresh response bodies in an owned Electron Page.
 * No URL routing, response fabrication, product header changes, cache deletion or remote allowance.
 * The caller's existing fixture timeout and owned-app teardown remain authoritative.
 */
'use strict';
const ENV_KEY = 'MV_E2E_NATIVE_FULL_BODY_READS';
function describe(error, renderingFaults) {
  try { return String(error); }
  catch (cause) { renderingFaults.push(cause); return '<exception rendering failed>'; }
}
function failureState(record, label) {
  let failed = false, primary;
  const secondary = [];
  const capture = cause => { if (!failed) { failed = true; primary = cause; } else secondary.push(cause); };
  return {
    capture,
    finish(extra) {
      const renderingFaults = [];
      const priorSecondary = secondary.slice();
      const summary = {label, ...extra, first_exception: failed ? describe(primary, renderingFaults) : null,
        secondary_exceptions: priorSecondary.map(cause => describe(cause, renderingFaults)),
        rendering_fault_count: renderingFaults.length};
      for (const cause of renderingFaults) capture(cause);
      try { record(summary); } catch (cause) { capture(cause); }
      if (failed) throw primary;
    },
  };
}
function createFixtureFailure(record) {
  return failureState(record, 'owned-native-full-body-fixture-closure');
}
async function withNativeFullBodyReads(page, use, record, envValue) {
  if (envValue !== '1') return use();
  const state = failureState(record, 'owned-native-CDP-full-body-cache-fence');
  const ledger = {environment_key: ENV_KEY, enabled: true, creation_attempted: false, session_created: false,
    network_enabled: false, cache_disabled_before_use: false, use_invoked: false, use_completed: false,
    cache_restore_attempted: false, cache_restored: false, network_disable_attempted: false,
    network_disabled: false, detach_attempted: false, detached: false, detach_uncertain: false,
    commands: [], all_owned_commands_settled: false, all_owned_CDP_sessions_closed: false,
    no_response_fabrication: true, no_product_cache_or_security_change: true};
  let owned, result;
  const send = async (session, method, parameters) => {
    const row = {method, parameters: parameters === undefined ? null : parameters, settled: false, succeeded: false};
    ledger.commands.push(row);
    try { await session.send(method, parameters); row.succeeded = true; }
    finally { row.settled = true; }
  };
  try {
    ledger.creation_attempted = true;
    owned = await page.context().newCDPSession(page);
    ledger.session_created = true;
    await send(owned, 'Network.enable'); ledger.network_enabled = true;
    await send(owned, 'Network.setCacheDisabled', {cacheDisabled: true}); ledger.cache_disabled_before_use = true;
    ledger.use_invoked = true; result = await use(); ledger.use_completed = true;
  } catch (cause) { state.capture(cause); }
  finally {
    if (owned !== undefined) {
      const session = owned;
      ledger.cache_restore_attempted = true;
      try { await send(session, 'Network.setCacheDisabled', {cacheDisabled: false}); ledger.cache_restored = true; }
      catch (cause) { state.capture(cause); }
      ledger.network_disable_attempted = true;
      try { await send(session, 'Network.disable'); ledger.network_disabled = true; }
      catch (cause) { state.capture(cause); }
      // Remove the owned handle before the one detach attempt; an uncertain detach is never retried.
      owned = undefined; ledger.detach_attempted = true;
      try { await session.detach(); ledger.detached = true; }
      catch (cause) { ledger.detach_uncertain = true; state.capture(cause); }
    }
    ledger.all_owned_commands_settled = ledger.commands.every(row => row.settled);
    ledger.all_owned_CDP_sessions_closed = ledger.session_created && ledger.detached && !ledger.detach_uncertain;
    state.finish(ledger);
  }
  return result;
}
module.exports = {ENV_KEY, createFixtureFailure, withNativeFullBodyReads};
