"""Client for the isolated Docker code-grading worker. Never executes submitted code here."""
import json
import os
import urllib.error
import urllib.request


def grade_code(technology, answer, hidden_tests):
    if technology not in {'Python', 'JavaScript', 'SQL'}:
        raise ValueError('Automated code grading is unavailable for this technology')
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 12000:
        raise ValueError('Invalid code answer')
    endpoint = os.environ.get('FSP_SANDBOX_URL', 'http://127.0.0.1:8766/grade')
    secret = os.environ.get('FSP_SANDBOX_SECRET', '')
    if not secret or not endpoint.startswith('http://127.0.0.1:'):
        raise RuntimeError('Dedicated sandbox worker is not configured')
    data = json.dumps({'technology': technology, 'code': answer, 'tests': hidden_tests}).encode()
    request = urllib.request.Request(endpoint, data=data, headers={'Content-Type': 'application/json',
                                     'Authorization': 'Bearer ' + secret}, method='POST')
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)
    if not isinstance(result.get('score'), (float, int)) or not 0 <= result['score'] <= 1:
        raise RuntimeError('Invalid sandbox worker response')
    return float(result['score'])
