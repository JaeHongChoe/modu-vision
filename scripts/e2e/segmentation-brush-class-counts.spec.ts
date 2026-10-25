import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {test,expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

test('saved two-class brush foreground counts survive browser import and project reopen',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const api=async(route:string,body?:unknown,method?:string)=>{
  const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});
  expect(response.ok(),await response.text()).toBe(true);return response.json();
 };
 const source=path.join(workspace.root,'brush-count-source');fs.mkdirSync(source,{recursive:true});
 const imagePath=path.join(source,'part.png');fs.writeFileSync(imagePath,png(256,3,(x,y)=>[x,y,80]));
 fs.writeFileSync(path.join(source,'part.json'),JSON.stringify({imagePath:'part.png',imageWidth:256,imageHeight:256,
  shapes:[{label:'Original',shape_type:'rectangle',points:[[1,1],[10,10]]}]}));
 const project=await api('/api/project/create',{name:'Brush class counts fixture',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');
 const annotations=await page.evaluate(()=>['defect','Scratch'].map((label,index)=>{
  const canvas=document.createElement('canvas');canvas.width=256;canvas.height=256;
  const ctx=canvas.getContext('2d')!;ctx.fillStyle='#ff8800';ctx.fillRect(30,30+index*80,70,20);
  return {type:'brush_mask',label,category_id:index+1,mask_rle:canvas.toDataURL('image/png')};
 }));
 await api('/api/annotations/save',{image_id:'part',image_path:imagePath,image_width:256,image_height:256,annotations});
 await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 await page.goto(renderer.url);
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Brush class counts fixture');
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
 await page.getByRole('button',{name:'집중 편집',exact:true}).click();
 await api('/api/dataset/metadata');await api('/api/team-data');
 const snapshot=()=>{
  const result:Record<string,string>={};
  const visit=(root:string)=>{for(const name of fs.readdirSync(root)){const file=path.join(root,name);
   if(fs.statSync(file).isDirectory())visit(file);
   else if(/\.(json|png)$/.test(name))result[file]=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  }};
  visit(source);visit(project.project_dir);return result;
 };
 const before=snapshot();
 const assertCounts=async()=>{
  expect((await api('/api/dataset/import',{folder_path:source,task:'segmentation'})).classes).toEqual({defect:1,Scratch:1});
  expect((await api(`/api/dataset/current-summary?folder_path=${encodeURIComponent(source)}&task=segmentation`)).classes).toEqual({defect:1,Scratch:1});
  expect((await api('/api/dataset/metadata/statistics')).classes).toEqual({defect:{count:1,ratio:1},Scratch:{count:1,ratio:1}});
  await expect(page.getByRole('button',{name:'defect 1',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Scratch 1',exact:true})).toBeVisible();
 };
 await assertCounts();
 await api('/api/project/open',{project_dir:project.project_dir});await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Brush class counts fixture');
 await assertCounts();expect(snapshot()).toEqual(before);
 await evidence.screenshot(page,'saved-brush-class-counts-reopened');
 evidence.note('brush_class_counts',{classes:{defect:1,Scratch:1},protected_before:before,protected_after:snapshot(),
  original_source_overlay_raster_policy_unchanged:true,generated_brush_fixture:true,actual_browser_class_chips:true,
  import_current_summary_statistics_project_reopen:true,registry_acceptance_credit:false,gpu_or_model_training:false});
});
