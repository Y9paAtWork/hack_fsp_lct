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
test('Actual PDF text extraction feeds skill parser',async()=>{
 const {readFile}=await import('node:fs/promises');
 const {getDocument}=await import('pdfjs-dist/legacy/build/pdf.mjs');
 const data=new Uint8Array(await readFile(new URL('./fixtures/resume.pdf',import.meta.url)));
 const task=getDocument({data,isEvalSupported:false});
 try{const doc=await task.promise;const page=await doc.getPage(1);const content=await page.getTextContent();assert.deepEqual(extractSkills(pageText(content.items)).skills,['Python','PostgreSQL','Docker','React Native']);}
 finally{await task.destroy();}
});
