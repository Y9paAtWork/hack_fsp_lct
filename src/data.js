export const directions = ['Backend', 'Frontend', 'Fullstack', 'ML / AI', 'Data Science', 'Data Engineering', 'Data Analytics', 'System Analysis', 'DevOps / SRE', 'QA', 'Mobile', 'Cybersecurity', 'System / Network', 'Database', 'Software Engineering', 'Other IT'];

export const candidate = {
  name: 'Мария Орлова', firstName: 'Мария', role: 'Backend Developer', grade: 'Middle',
  stack: ['Python', 'Django', 'PostgreSQL', 'Docker'], score: 84, completion: 86,
  experience: [
    {company: 'Компания А', period: '2023–2026', role: 'Backend Developer', description: 'Разработка сервисов обработки данных и внутренних API.', projects: [{name: 'Сервис аналитики', description: 'Разработка API для обработки и анализа данных.', tasks: 'Проектирование API, оптимизация запросов, интеграция сервисов.'}]},
    {company: 'Компания Б', period: '2021–2023', role: 'Python Developer', description: 'Разработка серверной части продукта и работа с базами данных.', projects: [{name: 'Платформа отчётности', description: 'Инструменты для внутренних команд.', tasks: 'Разработка модулей и поддержка интеграций.'}]}
  ],
  achievements: [
    {year: '2026', title: 'Чемпионат ФСП', place: '2 место', medal: '🥈'},
    {year: '2025', title: 'Кубок ФСП', place: '1 место', medal: '🥇'},
    {year: '2025', title: 'Региональный этап', place: '3 место', medal: '🥉'}
  ]
};

export const vacancies = [
  {id: 'backend', title: 'Backend Developer', specialization: 'Backend', grade: 'Middle', stack: ['Python', 'Django', 'PostgreSQL', 'Docker'], salary: '200 000 — 300 000 ₽', company: 'Компания А', description: 'Разработка API и сервисов обработки данных для внутренней платформы.', requirements: 'Опыт разработки серверных сервисов, работы с базами данных и интеграциями.'},
  {id: 'frontend', title: 'Frontend Developer', specialization: 'Frontend', grade: 'Middle', stack: ['React', 'TypeScript', 'CSS'], salary: '180 000 — 280 000 ₽', company: 'Компания А', description: 'Развитие интерфейса продукта и личных кабинетов.', requirements: 'Опыт работы над пользовательскими интерфейсами и компонентными системами.'},
  {id: 'data', title: 'Data Scientist', specialization: 'Data Science', grade: 'Junior', stack: ['Python', 'Pandas', 'SQL'], salary: '150 000 — 230 000 ₽', company: 'Компания А', description: 'Исследование данных и создание моделей для продуктовой аналитики.', requirements: 'Опыт анализа данных, подготовки признаков и оценки качества моделей.'}
];

const similarities = [0.94, 0.91, 0.89, 0.87, 0.85, 0.83, 0.82, 0.80, 0.78, 0.76, 0.75, 0.73, 0.71, 0.69, 0.67, 0.65, 0.63, 0.60, 0.57, 0.54];
export function rankedPeople(vacancy) {
  return similarities.map((similarity, index) => ({
    id: `${vacancy.id}-${index + 1}`,
    name: vacancy.id === 'backend' && index === 0 ? candidate.name : `Кандидат ${String(index + 1).padStart(2, '0')}`,
    role: vacancy.title,
    grade: vacancy.grade,
    similarity,
    score: Math.max(61, 94 - index * 2),
    awards: index < 8 ? Math.max(1, 12 - index) : 0,
    stack: index % 3 === 0 ? [...vacancy.stack] : vacancy.stack.slice(0, Math.max(2, vacancy.stack.length - 1)),
    skills: vacancy.stack.map((_, j) => Math.max(40, 94 - index * 2 - j * 6))
  }));
}

export const initialThreads = [
  {id:'thread-1',kind:'invitation',company:'Компания А',candidateName:candidate.name,role:'Backend Developer',salary:'250 000 — 320 000 ₽',specialty:'Backend · Middle',stack:'Python, Django, PostgreSQL',status:'Новое',time:'2 часа назад',messages:[{id:1,from:'employer',text:'Здравствуйте! Нам понравился ваш опыт разработки backend-сервисов. Будем рады обсудить предложение.',time:'10:42'}]},
  {id:'thread-2',kind:'invitation',company:'Компания Б',candidateName:candidate.name,role:'Backend Developer',salary:'220 000 — 280 000 ₽',specialty:'Backend · Middle',stack:'Python, PostgreSQL',status:'Просмотрено',time:'вчера',messages:[{id:2,from:'employer',text:'Добрый день! Приглашаем вас поговорить о проекте нашей команды.',time:'Вчера'}]},
  {id:'thread-3',kind:'response',company:'Компания В',candidateName:candidate.name,role:'Python Developer',salary:'210 000 — 270 000 ₽',specialty:'Backend · Middle',stack:'Python, FastAPI, PostgreSQL',status:'В диалоге',time:'сегодня',messages:[{id:3,from:'candidate',text:'Здравствуйте! Откликнулась на вашу вакансию Python Developer. Могу рассказать о своём опыте.',time:'09:15'},{id:4,from:'employer',text:'Спасибо за отклик! Поделитесь, пожалуйста, самым интересным проектом.',time:'09:42'}]},
  {id:'thread-4',kind:'response',company:'Компания Г',candidateName:candidate.name,role:'Backend Engineer',salary:'240 000 — 310 000 ₽',specialty:'Backend · Middle',stack:'Python, Docker, SQL',status:'В диалоге',time:'вчера',messages:[{id:5,from:'candidate',text:'Добрый день! Хотела бы обсудить вашу вакансию Backend Engineer.',time:'Вчера'},{id:6,from:'employer',text:'Привет! Давайте обсудим ваши задачи и наш проект.',time:'Вчера'}]}
];

export const dailyQuestions = [
  {question: 'Что помогает уменьшить число повторных запросов к базе данных?', answers: ['Кэширование', 'Увеличение размера ответа', 'Удаление индексов'], correct: 0},
  {question: 'Какой HTTP-метод обычно применяют для получения ресурса?', answers: ['GET', 'DELETE', 'PATCH'], correct: 0},
  {question: 'Что обозначает индекс в базе данных?', answers: ['Структуру для ускорения поиска', 'Резервную копию таблицы', 'Формат сетевого запроса'], correct: 0}
];

export const activityDays = Array.from({length: 364}, (_, index) => {
  const value = (index * 17 + Math.floor(index / 7) * 11) % 23;
  return {level: value < 10 ? 0 : value < 16 ? 1 : value < 20 ? 2 : 3, test: value === 21 || value === 22};
});
