import {validateResume,extractSkills} from './ner';
import {extractExperience} from './experience';
import {pageText,cleanResumeText} from './pdfText';
export async function readResume(file) {
  validateResume(file);
  let text;
  const warnings=[];
  if(/\.txt$/i.test(file.name))text=await file.text();
  else {
    const bytes=new Uint8Array(await file.arrayBuffer());
    if(!new TextDecoder().decode(bytes.slice(0,1024)).includes('%PDF-'))throw new Error('Файл не является PDF');
    const pdfjs=await import('pdfjs-dist');
    const {default:worker}=await import('pdfjs-dist/build/pdf.worker.min.mjs?url');
    pdfjs.GlobalWorkerOptions.workerSrc=worker;
    const task=pdfjs.getDocument({data:bytes,isEvalSupported:false});
    try {
      const doc=await task.promise;
      if(doc.numPages>50)throw new Error('В резюме должно быть не более 50 страниц');
      const pages=[];
      for(let i=1;i<=doc.numPages;i++){
        const page=await doc.getPage(i);
        const content=await page.getTextContent();
        const value=pageText(content.items);
        if(!value.trim())warnings.push(`На странице ${i} нет текста: возможно, требуется OCR.`);
        pages.push(value);
        page.cleanup();
      }
      text=pages.join('\n\n');
    }catch(e){
      if(e.name==='PasswordException')throw new Error('PDF защищён паролем. Загрузите копию без пароля.');
      if(e.name==='InvalidPDFException')throw new Error('PDF повреждён или имеет неподдерживаемый формат.');
      throw e;
    }finally{await task.destroy();}
  }
  text=cleanResumeText(text);
  if(!text)throw new Error('Текст не найден. Для скана нужен OCR; загрузите текстовый PDF или TXT.');
  return {...extractSkills(text),experience:extractExperience(text),warnings};
}
