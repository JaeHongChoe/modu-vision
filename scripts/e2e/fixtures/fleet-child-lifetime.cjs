"use strict";
// Retain the exit event of this exact original fixture ChildProcess at spawn.
function retainOriginalChildExit(child) {
  let resolve;
  const lifetime = {child, observed: false, ended: new Promise(done => {resolve = done;})};
  child.once('exit', () => {lifetime.observed = true; resolve();});
  return lifetime;
}

async function disconnectOriginalChild(agent) {
  const child = agent.child;
  if (!child) return;
  const lifetime = agent.childExit;
  if (!lifetime || lifetime.child !== child)
    throw new Error('Original fleet child exit handle is unavailable or replaced');
  if (!lifetime.observed && child.exitCode === null && child.signalCode === null)
    child.kill('SIGTERM');
  // Neither kill(false) nor exitCode alone proves that the retained exit fired.
  await lifetime.ended;
  if (!lifetime.observed)
    throw new Error('Original fleet child exit was not observed');
  if (agent.child !== child || agent.childExit !== lifetime)
    throw new Error('Original fleet child replaced during disconnect');
  agent.child = null;
  agent.childExit = null;
}

module.exports = {retainOriginalChildExit, disconnectOriginalChild};
