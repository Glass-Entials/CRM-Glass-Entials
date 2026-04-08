"""
Indian number system: amount to words converter.
Supports up to crores. Used for "Total in Words" on quotation PDF.
"""

ONES = [
    '', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
    'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
    'Seventeen', 'Eighteen', 'Nineteen'
]

TENS = [
    '', '', 'Twenty', 'Thirty', 'Forty', 'Fifty',
    'Sixty', 'Seventy', 'Eighty', 'Ninety'
]


def _two_digits(n: int) -> str:
    if n == 0:
        return ''
    if n < 20:
        return ONES[n]
    return TENS[n // 10] + ((' ' + ONES[n % 10]) if n % 10 else '')


def _three_digits(n: int) -> str:
    if n == 0:
        return ''
    hundred = n // 100
    rest = n % 100
    parts = []
    if hundred:
        parts.append(ONES[hundred] + ' Hundred')
    if rest:
        parts.append(_two_digits(rest))
    return ' '.join(parts)


def number_to_words(amount: float) -> str:
    """Convert a float amount to Indian English words."""
    amount = round(amount, 2)
    rupees = int(amount)
    paise = round((amount - rupees) * 100)

    if rupees == 0 and paise == 0:
        return 'Zero Rupees Only'

    parts = []

    crore = rupees // 10_000_000
    rupees %= 10_000_000
    lakh = rupees // 100_000
    rupees %= 100_000
    thousand = rupees // 1000
    rupees %= 1000
    remainder = rupees

    if crore:
        parts.append(_three_digits(crore) + ' Crore')
    if lakh:
        parts.append(_three_digits(lakh) + ' Lakh')
    if thousand:
        parts.append(_three_digits(thousand) + ' Thousand')
    if remainder:
        parts.append(_three_digits(remainder))

    result = 'Rupees ' + ' '.join(parts)
    if paise:
        result += f' and {_two_digits(paise)} Paise'
    result += ' Only'
    return result


if __name__ == '__main__':
    tests = [0, 1, 19, 100, 1000, 15000, 150000, 1500000, 15000000, 123456789.50]
    for t in tests:
        print(f'{t:>15} → {number_to_words(t)}')
