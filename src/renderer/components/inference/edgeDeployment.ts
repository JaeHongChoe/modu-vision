export type FlowDeploymentProfile = 'standard' | 'edge_cpu' | 'edge_cuda';
export interface EdgeTarget {
  os: 'linux' | 'windows' | 'macos';
  architecture: 'x86_64' | 'arm64';
}

export function flowDeploymentOptions(profile: FlowDeploymentProfile, target: EdgeTarget) {
  return profile !== 'standard'
    ? { deployment_profile: profile, target_os: target.os, target_arch: target.architecture }
    : {};
}

export function canVerifyFlowOnHost(profile: FlowDeploymentProfile, target: EdgeTarget, host: EdgeTarget | null): boolean {
  return profile === 'standard' || (profile==='edge_cpu' && host !== null && target.os === host.os && target.architecture === host.architecture);
}

export function edgeDeploymentCommands(target: EdgeTarget) {
  const python = target.os === 'windows' ? '.\\.edge_venv\\Scripts\\python.exe' : '.edge_venv/bin/python';
  const image = target.os === 'windows' ? 'C:\\inputs\\image.png' : '/absolute/path/image.png';
  return {
    install: 'python edge.py install',
    preflight: `${python} edge.py preflight`,
    run: `${python} edge.py run -- --image "${image}" --output result.json`,
  };
}
