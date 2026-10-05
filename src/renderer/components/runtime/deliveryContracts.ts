export type AdapterConfig={enabled:boolean;modbus:Record<string,unknown>|null;mes:Record<string,unknown>|null;clear_mes_token?:boolean};
export type AdapterForm={enabled:boolean;modbusEnabled:boolean;mesEnabled:boolean;host:string;port:string;unit:string;resultRegister:string;ackRegister:string;sequenceRegister:string;byteOrder:string;triggerRegister:string;triggerImage:string;timeout:string;ackTimeout:string;mesUrl:string;mesTimeout:string;clearToken:boolean};
export function adapterFormFromConfig(value:AdapterConfig):AdapterForm{
 const plc=value.modbus,mes=value.mes;
 return {enabled:value.enabled,modbusEnabled:!!plc,mesEnabled:!!mes,host:String(plc?.host??'127.0.0.1'),port:String(plc?.port??502),unit:String(plc?.unit_id??1),resultRegister:String(plc?.result_register??10),ackRegister:String(plc?.ack_register??11),sequenceRegister:plc?.sequence_register===null?'':String(plc?.sequence_register??12),byteOrder:String(plc?.byte_order??'big'),triggerRegister:plc?.trigger_register==null?'':String(plc.trigger_register),triggerImage:String(plc?.trigger_image_path??''),timeout:String(plc?.timeout??2),ackTimeout:String(plc?.ack_timeout??5),mesUrl:String(mes?.url??'http://127.0.0.1:9000/inspection'),mesTimeout:String(mes?.timeout??5),clearToken:value.clear_mes_token===true};
}
function number(value:string,label:string,minimum:number,maximum:number,integer=true):number{const result=Number(value);if(!value.trim()||!Number.isFinite(result)||result<minimum||result>maximum||(integer&&!Number.isInteger(result)))throw new Error(`${label}: 유효한 숫자를 입력하세요.`);return result;}
export function adapterConfigFromForm(form:AdapterForm,previous:AdapterConfig):AdapterConfig{
 if(!['big','little'].includes(form.byteOrder))throw new Error('16-bit 판정 payload byte order를 선택하세요.');
 const modbus=form.modbusEnabled?{...previous.modbus,host:form.host.trim(),port:number(form.port,'포트',1,65535),unit_id:number(form.unit,'Unit ID',0,255),result_register:number(form.resultRegister,'결과 레지스터',0,65535),ack_register:number(form.ackRegister,'ACK 레지스터',0,65535),sequence_register:form.sequenceRegister.trim()?number(form.sequenceRegister,'순번 레지스터',0,65535):null,timeout:number(form.timeout,'응답 제한',.001,30,false),ack_timeout:number(form.ackTimeout,'ACK 제한',.001,60,false)}:null;
 if(modbus){
  if(previous.modbus?.byte_order!==undefined||form.byteOrder!=='big')Object.assign(modbus,{byte_order:form.byteOrder});
  if(previous.modbus?.trigger_register!==undefined||form.triggerRegister.trim())Object.assign(modbus,{trigger_register:form.triggerRegister.trim()?number(form.triggerRegister,'트리거 레지스터',0,65535):null});
  if(previous.modbus?.trigger_image_path!==undefined||form.triggerImage.trim())Object.assign(modbus,{trigger_image_path:form.triggerImage.trim()||null});
 }
 const mes=form.mesEnabled?{...previous.mes,url:form.mesUrl.trim(),timeout:number(form.mesTimeout,'HTTP 제한',.001,60,false)}:null;
 return {enabled:form.enabled,modbus,mes,clear_mes_token:form.clearToken};
}
export function packageHandoff(value:{integrity:string;scope_matches:boolean;package_id?:string;package_path?:string;manifest_sha256?:string}){
 if(value.integrity!=='verified')throw new Error('패키지 무결성을 확인하세요.');if(!value.scope_matches)throw new Error('현재 프로젝트 소스에 맞는 패키지를 선택하세요.');
 return {packageId:value.package_id,packagePath:value.package_path,manifestSHA:value.manifest_sha256};
}
export function deviceStateLabel(row:{configured:boolean;live_verified:boolean;approved:boolean}){return row.live_verified?(row.approved?'실행 확인 · 승인 적용':'실제 실행 확인 · 운영 승인 전'):(row.configured?'설정 가능 · 실행 미확인':'환경 준비 필요 · 실행 미확인');}
