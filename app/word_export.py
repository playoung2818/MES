from datetime import datetime, timezone


def word_context(saved=None, preview=False):
    date = saved.generated_date if saved else datetime.now(timezone.utc)
    number = saved.document_number if saved else None
    if preview and saved is None:
        from .document_numbers import next_document_number
        number = next_document_number(date)
    return {'word_document_number': number,
            'word_date': date.strftime('%m/%d/%Y') if date else 'Not recorded'}
