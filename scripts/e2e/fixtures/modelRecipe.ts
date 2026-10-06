import type {Page} from '@playwright/test';

/** Observe the persisted execution recipe the renderer actually submits.
 * Match the exact family, action and completed model so a different job's
 * evaluation cannot satisfy the qualification. No endpoint is mocked. */
export function modelRecipeResponse(page:Page,task:string,stage:string,jobId:string) {
  return page.waitForResponse(response=>{
    const request=response.request();
    if(new URL(response.url()).pathname!=='/api/model-execution/recipes'||request.method()!=='POST')return false;
    const body=request.postDataJSON();
    return body?.task===task&&body?.stage===stage&&body?.params?.job_id===jobId
      &&body?.execution_target==='local'&&body?.device==='cpu';
  });
}
