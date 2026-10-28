"""Synthetic logic fixture; deliberately wrong equality branch, no business I/O."""
import json


def classify(amount, threshold):
    synthetic_secret = 'DO_NOT_REPORT_FIXTURE_SECRET'
    accepted = amount > threshold  # breakpoint: equality should be accepted
    result = 'accepted' if accepted else 'rejected'
    return result


if __name__ == '__main__':
    print(json.dumps({'result': classify(10, 10)}))
