import React, {useLayoutEffect, useRef, useState} from 'react';
import {getApiPersistenceIdentity, getProjectContext, request, type ProjectContext} from '../../services/api';

function sameContext(left: ProjectContext | null, right: ProjectContext | null): boolean {
  return left === right || Boolean(left && right && left.workspace_id === right.workspace_id
    && left.project_id === right.project_id && left.actor_id === right.actor_id && left.mode === right.mode);
}

function thumbnailPath(value: string): boolean {
  const prefix = '/api/dataset/thumbnail/';
  if (!value.startsWith(prefix) || /[\\\u0000-\u001f#]/.test(value.split('?')[0]) || /[\u0000-\u001f#]/.test(value)) return false;
  try {
    const parsed = new URL(value, 'http://thumbnail.invalid');
    return parsed.origin === 'http://thumbnail.invalid' && parsed.pathname.startsWith(prefix)
      && parsed.pathname.length > prefix.length && !parsed.hash;
  } catch { return false; }
}

interface ThumbnailRead {
  path: string;
  context: ProjectContext | null;
  current: () => boolean;
  publish: (url: string) => void;
  failed: (error: unknown) => void;
}

/** Own one lazy fetch and its Blob URL; no remote URL reaches an image element. */
export function createScopedThumbnailRead(options: ThumbnailRead): {observe: (element: Element) => void; retire: () => void} {
  const captured = options.context ? {...options.context} : null;
  const transport = getApiPersistenceIdentity();
  const controller = new AbortController();
  let retired = false, observed = false, started = false;
  let observer: IntersectionObserver | null = null, objectURL: string | null = null;
  const current = () => !retired && options.current() && transport === getApiPersistenceIdentity()
    && sameContext(captured, getProjectContext());
  const start = async () => {
    if (started || !current() || !thumbnailPath(options.path) || (transport !== 'local' && !captured)) return;
    started = true;
    try {
      const blob = await request<Blob>(options.path, {responseType: 'blob', projectContext: captured, signal: controller.signal});
      if (!current()) return;
      const created = URL.createObjectURL(blob);
      if (!current()) { URL.revokeObjectURL(created); return; }
      objectURL = created;
      options.publish(created);
    } catch (error) {
      if (current() && !controller.signal.aborted) options.failed(error);
    }
  };
  return {
    observe(element) {
      if (observed || !current() || !thumbnailPath(options.path) || (transport !== 'local' && !captured)
          || typeof IntersectionObserver === 'undefined') return;
      observed = true;
      // Read thumbnails that are near the viewport too, as the browser's own lazy images do: a gallery
      // row just below the fold is ready when the user scrolls to it instead of showing empty boxes.
      observer = new IntersectionObserver(entries => {
        if (!current() || !entries.some(entry => entry.target === element && entry.isIntersecting)) return;
        observer?.disconnect();
        void start();
      }, {rootMargin: '800px 0px'});
      observer.observe(element);
    },
    retire() {
      if (retired) return;
      retired = true;
      observer?.disconnect();
      controller.abort();
      if (objectURL) { URL.revokeObjectURL(objectURL); objectURL = null; }
    },
  };
}

interface Props {
  path: string;
  authority: string;
  context: ProjectContext | null;
  current: () => boolean;
  alt: string;
  className?: string;
  onLoad?: React.ReactEventHandler<HTMLImageElement>;
}

export const ScopedDatasetThumbnail: React.FC<Props> = ({path, authority, context, current, alt, className, onLoad}) => {
  const image = useRef<HTMLImageElement>(null);
  const [loaded, setLoaded] = useState<{key: string; url: string | null; failed: boolean} | null>(null);
  const captured = context ? {...context} : null;
  const contextKey = JSON.stringify(captured ? [captured.workspace_id, captured.project_id, captured.actor_id, captured.mode] : null);
  const key = JSON.stringify([authority, path, contextKey]);
  const eligible = current() && sameContext(captured, getProjectContext());
  useLayoutEffect(() => {
    setLoaded(null);
    const element = image.current;
    if (!element || !current()) return;
    const read = createScopedThumbnailRead({path, context: captured, current,
      publish: url => setLoaded({key, url, failed: false}),
      failed: () => setLoaded({key, url: null, failed: true})});
    read.observe(element);
    return () => { element.removeAttribute('src'); read.retire(); };
  }, [key, current]);
  const displayed = eligible && loaded?.key === key ? loaded.url || undefined : undefined;
  const failed = eligible && loaded?.key === key && loaded.failed;
  return <img ref={image} src={displayed} alt={alt} className={className} loading="lazy" decoding="async"
    data-read-state={failed ? 'failed' : displayed ? 'ready' : 'pending'}
    data-thumbnail-path={path}
    onLoad={event => { if (displayed && current() && event.currentTarget.src === displayed) onLoad?.(event); }}
    onError={() => { if (displayed && current()) setLoaded({key, url: null, failed: true}); }}/>
};
