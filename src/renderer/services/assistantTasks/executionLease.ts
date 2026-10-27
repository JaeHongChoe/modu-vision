/** In-process mutual exclusion for the two automatic writers. It never owns manual edits or a backend job. */
export type ExecutionLeaseOwner = 'automatic' | 'assistant';
let active: {owner: ExecutionLeaseOwner; token: symbol} | null = null;

/** Synchronous and non-reentrant: the caller owns the lease before its first await, or does no work. */
export function acquireExecutionLease(owner: ExecutionLeaseOwner): symbol | null {
  if (active || (owner !== 'automatic' && owner !== 'assistant')) return null;
  const token = Symbol(owner);
  active = {owner, token};
  return token;
}
export function releaseExecutionLease(token: symbol): void {
  if (active?.token === token) active = null;
}
export function executionLeaseOwner(): ExecutionLeaseOwner | null { return active?.owner ?? null; }
export function isExecutionLeaseBusy(): boolean { return active !== null; }
