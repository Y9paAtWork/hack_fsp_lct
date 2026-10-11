import test from 'node:test';
import assert from 'node:assert/strict';
import {pageText,cleanResumeText} from '../src/pdfText.js';
import {extractSkills} from '../src/ner.js';
const item=(str,x,y,width)=>({str,transform:[1,0,0,1,x,y],width,height:10});
test('PDF coordinates restore line and fragment order',()=>{
 const text=pageText([item('Docker',0,80,35),item('thon',12,100,25),item('Py',0,100,12),item('SQL',70,100,20)]);
 assert.equal(text,'Python SQL\nDocker');assert.deepEqual(extractSkills(text).skills,['Python','SQL','Docker']);
});
test('PDF whitespace, soft hyphens and line-break hyphenation',()=>{
 const text=cleanResumeText('  Post-\n greSQL\u00a0\u200b Py\u00adthon  ');assert.equal(text,'PostgreSQL Python');assert.deepEqual(extractSkills(text).skills,['PostgreSQL','Python']);
});
test('Empty PDF page yields empty text',()=>assert.equal(pageText([{type:'beginMarkedContent'}]),''));
function fixturePdf(){
 const content='BT /F1 12 Tf 72 720 Td (Python PostgreSQL Docker React Native) Tj ET';
 const objects=[
  '<< /Type /Catalog /Pages 2 0 R >>',
  '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
  '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
  '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  `<< /Length ${Buffer.byteLength(content)} >>\nstream\n${content}\nendstream`,
 ];
 let pdf='%PDF-1.4\n';const offsets=[0];
 objects.forEach((object,i)=>{offsets.push(Buffer.byteLength(pdf));pdf+=`${i+1} 0 obj\n${object}\nendobj\n`});
 const xref=Buffer.byteLength(pdf);pdf+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`;
 offsets.slice(1).forEach(offset=>{pdf+=`${String(offset).padStart(10,'0')} 00000 n \n`});
 pdf+=`trailer\n<< /Size ${objects.length+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
 return new Uint8Array(Buffer.from(pdf));
}
test('Actual PDF text extraction feeds skill parser',async()=>{
 const {getDocument}=await import('pdfjs-dist/legacy/build/pdf.mjs');
 const task=getDocument({data:fixturePdf(),isEvalSupported:false});
 try{const doc=await task.promise;const page=await doc.getPage(1);const content=await page.getTextContent();assert.deepEqual(extractSkills(pageText(content.items)).skills,['Python','PostgreSQL','Docker','React Native']);}
 finally{await task.destroy();}
});
