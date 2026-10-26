import {test} from './fixtures/test';
import {exerciseSavedThresholdOwnership} from './fixtures/savedEvaluationThresholdOwnership';
test.use({viewport:{width:2560,height:1440},actionTimeout:10000});
test('saved threshold owns settled null status and decoded raster through preview, pending, failure and late responses',async({page,workspace,evidence,renderer})=>{
 await exerciseSavedThresholdOwnership(page,workspace,evidence,renderer.origin,renderer.url);
});
