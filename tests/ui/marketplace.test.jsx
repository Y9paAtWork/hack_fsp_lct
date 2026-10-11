import React from 'react';
import {describe,it,expect,afterEach,vi} from 'vitest';
import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react';
import {AuthProvider} from '../../src/AuthContext';
import {ProfileProvider} from '../../src/ProfileContext';
import {CompanyProfile,CandidateVacancies,Conversation,InteractionInbox} from '../../src/Marketplace';

const response=(data,status=200)=>({ok:status<400,status,json:async()=>data});
afterEach(()=>{cleanup();vi.restoreAllMocks()});

describe('Marketplace screens',()=>{
 it('keeps the company profile read only until edit and confirms save',async()=>{
  let company={company_name:'Acme',about:'Разработка платформы',website:'acme.test'};
  global.fetch=vi.fn(async(path,options={})=>{
   if(path==='/api/employer/profile'&&options.method==='PUT'){company=JSON.parse(options.body);return response(company)}
   if(path==='/api/employer/profile')return response(company);
   return response({});
  });
  render(<CompanyProfile/>);
  await screen.findByText('Разработка платформы');
  expect(screen.queryByLabelText('Компания')).toBeNull();
  fireEvent.click(screen.getByText('Редактировать'));
  fireEvent.change(screen.getByLabelText('Компания'),{target:{value:'Acme Labs'}});
  fireEvent.click(screen.getByText('Сохранить'));
  await screen.findByText('Профиль компании сохранён.');
  expect(screen.queryByLabelText('Компания')).toBeNull();
  expect(company.company_name).toBe('Acme Labs');
 });
 it('shows ranked open vacancies, catalog switch and records an application',async()=>{
  const user={id:'u1',email:'user@example.org',name:'Анна',role:'candidate'};
  const profile={version:1,name:'Анна',email:user.email,step:4,selected:['Backend'],active:'Backend',stacks:{Backend:['Python']},jobs:{},ownProjects:{}};
  let applications=[];
  const calls=[];
  global.fetch=vi.fn(async(path,options={})=>{
   calls.push(path);
   if(path==='/api/auth/me')return response({user});
   if(path==='/api/profile')return response({profile,revision:0});
   const vacancy={id:'v1',vacancy_name:'Backend Engineer',specialization:'Backend',region:'Москва',description:'Разработка сервисов',responsibilities:'Создание API',position_requirements:'Python',all_technologies_json:'["Python"]',company_name:'Acme',employer_id:'e1'};
   if(path==='/api/candidate/vacancies/search')return response({items:[{...vacancy,score:.812,components:{experience:.91,skills:.5},matched_technologies:1,requested_technologies:1,best_experience:{title:'API проект'}}],total:1,experience_blocks:1,experience_pending:0,stale_vacancies:0});
   if(path.startsWith('/api/public/vacancies'))return response({items:[vacancy],total:1,ranking_status:'placeholder'});
   if(path==='/api/interactions')return response({items:applications});
   if(path==='/api/vacancies/v1/apply'){applications=[{id:'i1',vacancy_id:'v1',kind:'application',status:'pending'}];return response({id:'i1',status:'pending'},201)}
   return response({});
  });
  render(<AuthProvider><ProfileProvider><CandidateVacancies/></ProfileProvider></AuthProvider>);
  await screen.findByText('Backend Engineer');
  expect(screen.getByText('Сходство 0.812')).toBeTruthy();
  expect(screen.getByText('Лучший блок: API проект')).toBeTruthy();
  expect(calls).toContain('/api/candidate/vacancies/search');
  fireEvent.click(screen.getByText('Откликнуться'));
  await screen.findByText('Отклик: Ожидает решения');
  expect(calls).toContain('/api/vacancies/v1/apply');
  fireEvent.click(screen.getByRole('button',{name:'Каталог вакансий'}));
  await waitFor(()=>expect(calls.some(path=>path.startsWith('/api/public/vacancies'))).toBe(true));
 });
 it('sends messages through the server for an approved interaction',async()=>{
  const user={id:'u1',email:'user@example.org',name:'Анна',role:'candidate'};
  let messages=[];
  global.fetch=vi.fn(async(path,options={})=>{
   if(path==='/api/auth/me')return response({user});
   if(path==='/api/interactions/i1' )return response({id:'i1',kind:'application',status:'approved',candidate_id:'u1',employer_id:'e1',candidate_name:'Анна',company_name:'Acme',vacancy_name:'Backend Engineer',specialization:'Backend',chat_enabled:true,messages});
   if(path==='/api/interactions/i1/messages'){const body=JSON.parse(options.body);messages=[...messages,{id:'m1',sender_id:'u1',sender_name:'Анна',body:body.body,created_at:'2026-10-11T12:00:00Z'}];return response(messages[0],201)}
   return response({});
  });
  render(<AuthProvider><Conversation role="candidate" id="i1"/></AuthProvider>);
  const field=await screen.findByLabelText('Сообщение');
  fireEvent.change(field,{target:{value:'Здравствуйте'}});
  fireEvent.click(screen.getByText('Отправить'));
  await screen.findByText('Здравствуйте');
 });
 it('does not expose standalone candidate search from invitations',async()=>{
  global.fetch=vi.fn(async(path)=>{
   if(path==='/api/interactions')return response({items:[]});
   return response({});
  });
  render(<InteractionInbox role="employer" kind="invitation"/>);
  await screen.findByText('Пока нет приглашений.');
  expect(screen.queryByText('Новое предложение')).toBeNull();
  expect(screen.queryByLabelText('Найти зарегистрированного кандидата')).toBeNull();
 });
 it('shows invitation salary and terms before the candidate accepts',async()=>{
  const user={id:'u1',email:'anna@example.org',name:'Анна',role:'candidate'};
  global.fetch=vi.fn(async path=>{
   if(path==='/api/auth/me')return response({user});
   if(path==='/api/interactions/i2')return response({id:'i2',kind:'invitation',status:'pending',
    candidate_id:'u1',employer_id:'e1',candidate_name:'Анна',company_name:'Acme',
    vacancy_name:'Предложение о работе',description:'Работа над внутренней платформой.',
    salary_min:150000,salary_max:200000,contact_method:'Чат на платформе',
    chat_enabled:false,messages:[]});
   return response({});
  });
  render(<AuthProvider><Conversation role="candidate" id="i2"/></AuthProvider>);
  await screen.findByText('Работа над внутренней платформой.');
  expect(screen.getByText(/Зарплата:.*₽\/мес/)).toBeTruthy();
  expect(screen.getByText('Чат откроется после одобрения.')).toBeTruthy();
  expect(screen.queryByLabelText('Сообщение')).toBeNull();
 });
});
