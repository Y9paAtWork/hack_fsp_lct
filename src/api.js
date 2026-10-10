export async function api(path,{method='GET',body,...options}={}){
 const response=await fetch('/api'+path,{method,credentials:'same-origin',headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined,...options});
 let data;try{data=await response.json();}catch{data={detail:response.ok?'':'Сервер недоступен. Запустите backend.'};}
 if(!response.ok){const error=new Error(typeof data.detail==='string'?data.detail:'Проверьте данные формы');error.status=response.status;throw error;}
 return data;
}
