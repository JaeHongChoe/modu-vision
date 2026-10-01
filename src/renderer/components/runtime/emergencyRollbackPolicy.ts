export type RollbackCapabilities = {
  can_rollback: boolean;
  can_emergency_rollback: boolean;
  actor_name: string;
  actor_role: string;
};
export type EmergencyRollbackEvent = {
  event_id: string;
  request_id: string;
  target_id: string;
  deployment_id: string;
  event: 'attempted' | 'denied' | 'rejected' | 'committed';
  actor_name: string;
  reason: string;
  detail?: string;
  result_deployment_id?: string;
  created_at: number;
};
export type EmergencyRollbackReceipt = {
  deployment_id: string;
  emergency: {request_id: string; event_id: string};
};
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function nonblank(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}
function sha256(value: unknown): value is string {
  return typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);
}
export function emergencyAcknowledged(targetId: string, receipt: unknown, state: unknown): boolean {
  if (!nonblank(targetId) || !record(receipt) || !record(receipt.emergency) || !record(state)
      || !record(state.target) || !record(state.active) || !record(state.active.release) || !record(state.runtime)
      || !Array.isArray(state.emergency_rollback_events)) return false;
  const emergency = receipt.emergency;
  const runtimeHash = state.runtime.manifest_sha256;
  const activeHash = state.active.release.manifest_sha256;
  return Boolean(nonblank(receipt.deployment_id) && nonblank(emergency.request_id) && nonblank(emergency.event_id)
    && state.target.target_id === targetId && state.active.deployment_id === receipt.deployment_id
    && state.matches_active === true && state.runtime.status === 'ready'
    && sha256(runtimeHash) && sha256(activeHash) && runtimeHash.toLowerCase() === activeHash.toLowerCase()
    && state.emergency_rollback_events.some(event => record(event) && event.target_id === targetId
      && event.event === 'committed' && event.request_id === emergency.request_id
      && event.event_id === emergency.event_id && event.result_deployment_id === receipt.deployment_id));
}
