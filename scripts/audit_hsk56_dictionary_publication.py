"""Audit both complete HSK5/6 first chapters and the shared dictionary graph.

Run after publishing and regenerating app assets:
    python -m scripts.audit_hsk56_dictionary_publication
"""
from scripts.audit_hsk34_dictionary_publication import audit


if __name__ == '__main__':
    audit(('hsk5', 'hsk6'))
