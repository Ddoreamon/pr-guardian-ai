"""Command line entry point."""

import sys

from .models import Product
from .service import discount_all, region_report, total


def main(argv):
    products = [Product("A-1", 10.0), Product("B-2", 20.0)]
    print("total:", total(products))
    batches = discount_all([[products[0], products[1]]])
    print("discounted:", batches[-1])
    region_report(argv[1])


if __name__ == "__main__":
    main(sys.argv)
