"""Store departments ("zones") and how H&M articles map onto them.

The departments follow a real H&M store: Ladies, Divided (young women), Men, Kids, plus Denim, Basics,
Accessories and the New-in area at the entrance. 'new_in' is not a fixed mapping: an article is "new"
for its first weeks on sale (see sim).
"""
ZONES = ["new_in", "ladies", "divided", "men", "kids", "denim", "basics", "accessories"]
ZONE_NAMES = {"new_in": "New in", "ladies": "Ladies", "divided": "Divided", "men": "Men", "kids": "Kids",
              "denim": "Denim", "basics": "Basics", "accessories": "Accessories"}

# expects columns garment_group_name and index_group_name from the articles table
ZONE_SQL = """CASE
  WHEN index_group_name = 'Baby/Children' THEN 'kids'
  WHEN garment_group_name = 'Trousers Denim' THEN 'denim'
  WHEN garment_group_name IN ('Accessories', 'Shoes') THEN 'accessories'
  WHEN garment_group_name IN ('Jersey Basic', 'Socks and Tights', 'Under-, Nightwear') OR index_group_name = 'Sport' THEN 'basics'
  WHEN index_group_name = 'Menswear' THEN 'men'
  WHEN index_group_name = 'Divided' THEN 'divided'
  ELSE 'ladies' END"""

# garments people usually try on before buying
TRY_ON_GARMENTS = ("Dresses Ladies", "Trousers", "Trousers Denim", "Blouses", "Knitwear", "Outdoor", "Skirts", "Shirts", "Dressed", "Shorts", "Swimwear")
NEW_ARTICLE_DAYS = 28
