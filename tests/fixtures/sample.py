"""Toy debuggee used by the e2e test."""


def compute(data):
    total = 0
    for value in data:
        total += value  # <- breakpoint target (line 7)
    return total


def main():
    numbers = [i * i for i in range(25)]
    label = "sum of squares"
    result = compute(numbers)
    print(f"{label}: {result}")


if __name__ == "__main__":
    main()
