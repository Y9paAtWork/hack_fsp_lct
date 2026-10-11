import unittest

from backend.ranking import catalog
from backend.technology_heuristics import extract_evidence, extract_technologies


class TechnologyHeuristicsTest(unittest.TestCase):
    def test_canonical_names_and_common_aliases(self):
        text = 'Нужен опыт с Python, React Native, k8s, Postgres, LLM и RAG. Знание 1С.'
        found = extract_technologies(text)
        self.assertEqual(found, ['Python', 'React Native', 'Kubernetes', 'PostgreSQL',
                                 'Generative AI / LLM', 'Search / Retrieval', '1C'])
        self.assertTrue(set(found).issubset(set(catalog()[0])))

    def test_boundaries_negation_and_ambiguous_words(self):
        text = 'Go to market. C-level. JavaScript и Java. Python не требуется. Язык R, опыт с Go.'
        found = extract_technologies(text)
        self.assertIn('JavaScript', found)
        self.assertIn('Java', found)
        self.assertIn('R', found)
        self.assertIn('Go', found)
        self.assertNotIn('C', found)
        self.assertNotIn('Python', found)

    def test_longest_match_and_evidence(self):
        hits = extract_evidence('Работа с Docker Compose, Spring Boot и Kaspersky Security Center.')
        found = [x['technology'] for x in hits]
        self.assertIn('Docker Compose', found)
        self.assertNotIn('Docker', found)
        self.assertIn('Spring Boot', found)
        self.assertNotIn('Spring', found)
        self.assertIn('Kaspersky Security Center', found)

    def test_deep_learning_abbreviation(self):
        self.assertIn('Deep Learning', extract_technologies('Технический стек: ML / DL / LLM / RAG'))


if __name__ == '__main__':
    unittest.main()
