from dataclasses import dataclass
from typing import List, Self

from numpy import concatenate
from numpy._typing import NDArray

SAMPLE_RATE = 24000


@dataclass
class Token:
    text_index_start: int
    text_index_end: int
    start_index: int
    end_index: int

    def offset(self, audio_offset: int) -> Self:
        self.start_index += audio_offset
        self.end_index += audio_offset
        return self


@dataclass
class Audio:
    data: NDArray
    tokens: List[Token]

    def concat(self, other: Self):
        self.tokens.extend([token.offset(len(self.data)) for token in other.tokens])
        self.data = concatenate((self.data, other.data))
