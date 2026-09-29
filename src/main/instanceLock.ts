import fs from 'fs';
import path from 'path';

type LockableApp = {
  setPath(name: 'userData', directory: string): void;
  requestSingleInstanceLock(): boolean;
};

export function acquireAppInstanceLock(app: LockableApp, userDataDir?: string): boolean {
  if (userDataDir) {
    if (!path.isAbsolute(userDataDir)) {
      throw new Error('VISION_AI_STUDIO_USER_DATA_DIR must be an absolute path');
    }
    fs.mkdirSync(userDataDir, { recursive: true, mode: 0o700 });
    app.setPath('userData', userDataDir);
  }
  return app.requestSingleInstanceLock();
}
