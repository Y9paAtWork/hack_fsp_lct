export async function api(path,{method='GET',body,...options}={}){
 const response=await fetch('/api'+path,{method,credentials:'same-origin',headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined,...options});
 let data;try{data=await response.json();}catch{data={detail:response.ok?'':'Сервер недоступен. Запустите backend.'};}
 if(!response.ok){const detail=typeof data.detail==='string'?data.detail:'Проверьте данные формы';const error=new Error(response.status===404&&detail.toLowerCase()==='not found'?'Информация пока недоступна.':detail);error.status=response.status;throw error;}
 return data;
}
