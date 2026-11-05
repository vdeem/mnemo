from mnemo.core import Mnemo
import time

mnemo = Mnemo(db_path="test_triples.db", chroma_path="./test_chroma_triples")

print("Adding episodes...")
mnemo.add("Jack is a dog.")
mnemo.add("User owns Jack.")
mnemo.add("Jack passed away today.")

time.sleep(1)
print("Consolidating...")
mnemo.consolidate()

print("\nRecall 'Jack':")
res = mnemo.recall("What happened to Jack?")
for f in res["facts"]:
    print(f"  {f['source']} --[{f['relation']}]--> {f['target']}")