import React from 'react';
import {describe,it,expect,afterEach,vi} from 'vitest';
import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react';
import {TechnologyTests,DailyPractice,GradeProgress} from '../../src/Assessments';
import DatabaseEmployer from '../../src/DatabaseEmployer';

afterEach(()=>{cleanup();vi.restoreAllMocks()});
const response=(data,status=200)=>({ok:status<400,status,json:async()=>data});

describe('Assessment screens',()=>{
 it('shows the declared Middle grade as the confirmation target',async()=>{
  global.fetch=vi.fn(async()=>response({specializations:{'ML / AI':{
   current_grade:'Middle',confirmed_grade:null,next_grade:'Middle',can_advance:false,
   levels:{Middle:{groups_done:0,groups_required:5,at_target_done:0,at_target_required:2,
    mandatory_first:3,groups:[]}}
  }}}));
  render(<GradeProgress/>);
  await screen.findByText(/Middle не подтверждён/);
  expect(screen.getByText(/Подтверждение уровня/)).toBeTruthy();
  expect(screen.queryByText(/Следующий уровень: Junior/)).toBeNull();
 });
 it('loads a real technology question and submits its answer to the server',async()=>{
  const calls=[];
  global.fetch=vi.fn(async(path,options={})=>{
   calls.push({path,body:options.body?JSON.parse(options.body):null});
   if(path==='/api/tests/technologies')return response({items:[{technology:'Git',grade:'Junior',questions:1,last_score:null,available:true}]});
   if(path==='/api/tests/technologies/start')return response({attempt_id:'a1',technology:'Git',grade:'Junior',questions:[{id:'q1',type:'single',text:'Что делает Git?',options:[{value:'A',label:'Версия A'},{value:'B',label:'Версия B'}]}]});
   if(path==='/api/tests/technologies/a1/submit')return response({score:100,technology:'Git',grade:'Junior',passed:true});
   return response({});
  });
  render(<TechnologyTests/>);
  expect(screen.queryByLabelText('Специализация')).toBeNull();
  fireEvent.click(await screen.findByText('Начать'));
  await screen.findByText('Что делает Git?');
  fireEvent.click(screen.getByText('Версия A'));
  fireEvent.click(screen.getByText('Завершить и проверить'));
  await screen.findByText('100 / 100');
  expect(calls.find(x=>x.path==='/api/tests/technologies/start').body).toEqual({technology:'Git',grade:'Junior'});
  expect(calls.find(x=>x.path==='/api/tests/technologies/a1/submit').body.answers).toEqual({q1:'A'});
 });
 it('uses the daily API and does not offer a second attempt after completion',async()=>{
  const calls=[];
  global.fetch=vi.fn(async(path,options={})=>{calls.push(path);
   if(path==='/api/tests/daily')return response({date_msk:'2026-10-11',specialization:'Backend',grade:'Junior',completed:false,questions:[1,2,3].map(id=>({id,type:'single',text:`Вопрос ${id}`,options:[{value:'A',label:`Ответ ${id}`}]}))});
   if(path==='/api/tests/daily/submit')return response({score:100});
   return response({});});
  render(<DailyPractice/>);
  for(let i=1;i<=3;i++)fireEvent.click(await screen.findByText(`Ответ ${i}`));
  fireEvent.click(screen.getByText('Завершить тест'));
  await screen.findByText('100 / 100');
  expect(screen.queryByText('Завершить тест')).toBeNull();
  expect(calls).toContain('/api/tests/daily/submit');
 });
 it('reruns real matching with verified filter before displaying top',async()=>{
  const calls=[];
  global.fetch=vi.fn(async(path)=>{calls.push(path);
   if(path.startsWith('/api/vacancies'))return response({items:[{id:'v1',vacancy_name:'Backend',specialization:'Backend',description:'API',status:'open'},{id:'v2',vacancy_name:'Archive',specialization:'Backend',description:'Old API',status:'closed'}]});
   if(path.startsWith('/api/matches'))return response({items:[{resume_id:'r1',candidate_name:'Ника',position_name:'Разработчик',score:.75,verified_grade:path.includes('verified_only=true')?'Junior':null}]});
   return response({});});
  render(<DatabaseEmployer page="search"/>);
  await screen.findByRole('option',{name:'Backend · Backend'});
  expect(screen.queryByRole('option',{name:'Archive · Backend'})).toBeNull();
  fireEvent.change(screen.getByLabelText('Вакансия для подбора'),{target:{value:'v1'}});
  fireEvent.click(screen.getByText('Запустить подбор'));
  await screen.findByText('Ника',{exact:false});
  fireEvent.click(screen.getByLabelText('Только подтверждённые'));
  await waitFor(()=>expect(calls.some(x=>x.includes('verified_only=true'))).toBe(true));
 });
});
