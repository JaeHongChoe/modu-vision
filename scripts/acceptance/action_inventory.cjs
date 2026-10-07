// Declared source paths only, never executed or accepted behavior evidence.
const fs=require('node:fs'),path=require('node:path');
const {buildInventory}=require('./source_action_inventory.cjs');
if(require.main===module){
 const root=path.resolve(__dirname,'../..'),output=process.argv[2];
 if(!output)throw Error('Provide a new output file');
 const program=JSON.parse(fs.readFileSync(path.join(root,'docs/service-upgrade-program.json'),'utf8'));
 const report=buildInventory({root,program});
 fs.writeFileSync(path.resolve(output),JSON.stringify(report,null,2)+'\n',{flag:'wx'});
 console.log(JSON.stringify({records:report.records.length,unique_ui_sources:report.sources.length,
  unique_actions:report.unique_actions,unique_keyboard_candidates:report.unique_keyboard_candidates,accepted:0}));
}
module.exports={buildInventory};
