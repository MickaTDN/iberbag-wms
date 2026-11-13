/* No server credentials or reusable encryption keys are persisted. PR39 adapters absent. */
const enc=new TextEncoder(),dec=new TextDecoder();
const request=r=>new Promise((yes,no)=>{r.onsuccess=()=>yes(r.result);r.onerror=()=>no(r.error);});
async function db(){const r=indexedDB.open('ddn-operator-v1',1);r.onupgradeneeded=()=>r.result.createObjectStore('vaults');return request(r);}
async function read(id){const d=await db();try{return await request(d.transaction('vaults').objectStore('vaults').get(id));}finally{d.close();}}
async function write(id,value){const d=await db();try{await new Promise((yes,no)=>{const t=d.transaction('vaults','readwrite');t.objectStore('vaults').put(value,id);t.oncomplete=yes;t.onabort=()=>no(t.error);t.onerror=()=>no(t.error);});}finally{d.close();}}
export const states={pending:'Pendente no dispositivo',sent:'Enviada',confirmed:'Confirmada pelo WMS',conflict:'Conflito/Recusada'};
export class Vault{
 constructor(user,context){this.user=user;this.context=context;this.id=`${user}:${context}`;this.key=null;}
 lock(){this.key=null;}
 async unlock(passphrase,authorize){
  if(!navigator.locks)throw Error('Navegador sem bloqueio local.');
  return navigator.locks.request('ddn-vault-'+this.id,()=>this.open(passphrase,authorize));
 }
 async open(passphrase,authorize){
  this.lock();const who=await authorize();if(who.user!==this.user||who.context!==this.context)throw Error('Identidade/contexto divergente.');
  if(typeof passphrase!=='string'||passphrase.length<14)throw Error('Frase local: mínimo 14 caracteres, distinta da palavra-passe WMS.');
  const old=await read(this.id),salt=old?.salt||crypto.getRandomValues(new Uint8Array(16));
  const base=await crypto.subtle.importKey('raw',enc.encode(passphrase),'PBKDF2',false,['deriveKey']);
  this.key=await crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations:600000,hash:'SHA-256'},base,{name:'AES-GCM',length:256},false,['encrypt','decrypt']);this.salt=salt;
  try{if(old)await this.get();else await this.save({schema:1,copy:null,queue:[]});}catch(e){this.lock();throw e;}
 }
 async get(){if(!this.key)throw Error('Cofre bloqueado. Reautenticar online.');const r=await read(this.id);if(!r)return {schema:1,copy:null,queue:[]};const value=JSON.parse(dec.decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:r.iv,additionalData:enc.encode(this.id)},this.key,r.cipher)));if(value.schema!==1||!Array.isArray(value.queue))throw Error('Versão local incompatível; não apagar ou substituir a fila.');return value;}
 async save(value){if(!this.key)throw Error('Cofre bloqueado.');const iv=crypto.getRandomValues(new Uint8Array(12));const cipher=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:enc.encode(this.id)},this.key,enc.encode(JSON.stringify(value)));await write(this.id,{salt:this.salt,iv,cipher});}
 async change(fn){if(!navigator.locks)throw Error('Navegador sem bloqueio transacional local.');return navigator.locks.request('ddn-vault-'+this.id,async()=>{const d=await this.get();const result=await fn(d);await this.save(d);return result;});}
 async copy(data){if(data.user!==this.user||data.context!==this.context)throw Error('Contexto/operador incorreto.');
 const clean={user:data.user,context:data.context,synced_at:data.synced_at,tasks:data.tasks.map(t=>({kind:t.kind,reference:t.reference,id:t.id,version:t.version,status:t.status,lines:t.lines.map(l=>Object.fromEntries(['article','lot','quantity','boxes','classification','location','status'].filter(k=>l[k]!==undefined).map(k=>[k,l[k]])))}))};
 if(JSON.stringify(clean).length>2000000)throw Error('Cópia excede limite local.');return this.change(d=>{d.copy=clean;});}
 async enqueue(action){
  // Generic infrastructure only; UI has no stock command, no production adapter.
  const {operation,reference,version,depends=[]}=action;
  if(!['prepare','load','putaway'].includes(operation)||typeof reference!=='string'||!reference||reference.length>120||!Number.isSafeInteger(version)||version<0||!Array.isArray(depends))throw Error('Operação local inválida.');
  return this.change(d=>{if(operation==='load'&&!depends.some(id=>d.queue.some(q=>q.id===id&&q.operation==='prepare'&&q.reference===reference)))throw Error('Carregamento exige preparação dependente explícita.');if(depends.some(id=>!d.queue.some(q=>q.id===id)))throw Error('Dependência inexistente.');if(d.queue.length>=1000)throw Error('Fila local cheia; preservar e resolver operações existentes.');const id=crypto.randomUUID();d.queue.push({id,user:this.user,context:this.context,operation,reference,version,depends,idempotency:crypto.randomUUID(),state:'pending'});return id;});
 }
 async sync(authorize,adapters={}){
  const who=await authorize();if(who.user!==this.user||who.context!==this.context)throw Error('Reautenticar no contexto original.');
  // No automatic retry of sent/conflicted actions; explicit review is required.
  for(const row of (await this.get()).queue){
   if(row.state!=='pending'||typeof adapters[row.operation]!=='function')continue;
   const current=await authorize();if(current.user!==this.user||current.context!==this.context)throw Error('Sessão alterada; ação continua pendente.');
   const ready=await this.change(d=>{const q=d.queue.find(q=>q.id===row.id);if(q.state!=='pending'||q.depends.some(id=>!d.queue.some(p=>p.id===id&&p.state==='confirmed')))return false;q.state='sent';return true;});
   if(!ready)continue;
   try{const reply=await adapters[row.operation](row);await this.change(d=>{d.queue.find(q=>q.id===row.id).state=reply.confirmed===true?'confirmed':'conflict';});}
   catch(e){throw Error('Envio interrompido; estado Enviada preservado. Rever antes de repetir.');}
  }
 }
 async retry(id,authorize){const who=await authorize();if(who.user!==this.user||who.context!==this.context)throw Error('Identidade inválida.');return this.change(d=>{const q=d.queue.find(q=>q.id===id);if(!q||!['sent','conflict'].includes(q.state))throw Error('Revisão inválida.');q.state='pending';});}
 async erase(confirmed){if(confirmed!==true)throw Error('Confirmação explícita obrigatória.');this.lock();const d=await db();await new Promise((yes,no)=>{const t=d.transaction('vaults','readwrite');t.objectStore('vaults').delete(this.id);t.oncomplete=yes;t.onabort=no;});d.close();}
}
