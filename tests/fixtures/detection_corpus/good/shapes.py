"""Dataclasses and properties."""
from dataclasses import dataclass, field
import math


@dataclass
class Circle:
    radius: float
    tags: list = field(default_factory=list)

    @property
    def area(self):
        return math.pi * self.radius ** 2

    def scale(self, factor):
        if factor <= 0:
            raise ValueError("factor must be positive")
        return Circle(self.radius * factor, list(self.tags))


def total_area(shapes):
    return sum(s.area for s in shapes)
