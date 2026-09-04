"""The one Porter2 stemmer both sides of the naming fit use (spec §9a). Needs snowballstemmer."""

import snowballstemmer

# Snowball (Porter2), applied in the IR layer (not in stdlib tokens.py) so morphological variants
# (reviewer/reviews/reviewing -> review) share a term before tf-idf. Shared by `naming.py`, which
# stems the documents, and `textsearch.py`, which stems the query: a query stemmed any other way
# would look up terms the stored vocabulary does not hold. No verb-synonym map: a POC measured it
# worthless; LSI discovers verb synonymy from co-occurrence.
STEM = snowballstemmer.stemmer("english").stemWord
