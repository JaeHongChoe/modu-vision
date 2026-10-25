import {useRef, useState} from 'react';

/** A late callback may finish after navigation; its message still belongs to the scope that started it. */
export function useScopedError(scope: string): [string | null, (text: string | null) => void] {
  const current = useRef({scope, revision: 0});
  if (current.current.scope !== scope) current.current = {scope, revision: current.current.revision + 1};
  const revision = current.current.revision;
  const [stored, setStored] = useState<{revision: number; text: string | null} | null>(null);
  return [stored?.revision === revision ? stored.text : null, text => {
    if (current.current.revision === revision) setStored({revision, text});
  }];
}
