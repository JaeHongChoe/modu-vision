/** The shared-server setup steps as exact commands from a few answers (audit F-17 / B4). Pure, so it can be tested.
 *  The commands never carry a password: the account tool asks for it hidden on the server itself. */
export type SetupAnswers = { accountsDir: string; projectsDir: string; port: number; admin: string; sshTarget: string };

/** POSIX single-quote escaping, so a path with spaces or quotes stays one argument. */
export const shellQuote = (value: string) => (/^[A-Za-z0-9_@%+=:,./-]+$/.test(value) ? value : `'${value.replace(/'/g, `'\\''`)}'`);

export function setupCommands(answers: SetupAnswers): { label: string; command: string; where: string }[] {
  const port = Number.isInteger(answers.port) && answers.port > 0 && answers.port < 65536 ? answers.port : 8000;
  return [
    { label: '1. 관리자 계정 만들기 (비밀번호는 숨겨 입력, 저장소에는 해시만)', where: '서버 컴퓨터',
      command: `python -m backend.accounts_cli --shared-auth-dir ${shellQuote(answers.accountsDir)} --username ${shellQuote(answers.admin)}` },
    { label: '2. 서버 시작 (이 컴퓨터 안에서만 열림)', where: '서버 컴퓨터',
      command: `python -m backend.main --host 127.0.0.1 --port ${port} --project-dir ${shellQuote(answers.projectsDir)} --shared-auth-dir ${shellQuote(answers.accountsDir)}` },
    { label: '3. 암호화된 SSH 터널 열기', where: '작업자 컴퓨터',
      command: `ssh -N -L ${port}:127.0.0.1:${port} ${shellQuote(answers.sshTarget)}` },
    { label: '4. 앱의 "공동 작업 서버 주소"에 입력', where: '작업자 컴퓨터', command: `http://127.0.0.1:${port}` },
  ];
}
