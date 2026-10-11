const bank = [
  {skill:'Python',question:'Какой тип в Python является неизменяемым?',options:['list','dict','tuple'],correct:2},
  {skill:'JavaScript',question:'Что возвращает async-функция?',options:['Promise','Всегда строку','Всегда undefined'],correct:0},
  {skill:'TypeScript',question:'Для чего служит interface?',options:['Запуск сервера','Описание формы объекта','Хранение пароля'],correct:1},
  {skill:'React',question:'Как обновить состояние useState?',options:['Изменить переменную напрямую','Вызвать setter','Обновить CSS'],correct:1},
  {skill:'PostgreSQL',question:'Как выбрать только уникальные значения?',options:['ORDER BY','DISTINCT','LIMIT'],correct:1},
  {skill:'SQL',question:'Какое условие проверяет отсутствие значения?',options:['= NULL','IS NULL','== None'],correct:1},
  {skill:'Docker',question:'Что описывает Dockerfile?',options:['Сборку образа','Список пользователей ОС','Только сетевые порты'],correct:0},
  {skill:'Git',question:'Какая команда показывает изменения файлов?',options:['git init','git diff','git clone'],correct:1},
  {skill:'Django',question:'Что делает Django ORM?',options:['Работает с БД через модели','Собирает CSS','Запускает контейнеры'],correct:0},
  {skill:'FastAPI',question:'Что используется для проверки схем данных?',options:['Pydantic','CSS','Git'],correct:0},
];
export function selectQuestions(skills) { return bank.filter(q => skills.includes(q.skill)); }
export function gradeQuiz(questions, answers) {
  if (!questions.length) return null;
  const correct = questions.filter((q, i) => answers[i] === q.correct).length;
  return {correct,total:questions.length,score:Math.round(100*correct/questions.length)};
}
