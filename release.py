import os
import subprocess
import shutil

tag_list = subprocess.check_output(["git", "tag"]).decode().split("\n")
print("Exists tags: " + str(tag_list))

name = ""

for f in os.listdir():
    if os.path.isdir(f) and f != ".git" and f != ".github":
        print("Find plugin " + f)
        if not f in tag_list:
            print(subprocess.run(["git", "checkout", "release_tags"]))
            print(subprocess.run(["git", "tag", f]))
            print(subprocess.run(["git", "push", "--tags"]))
            print(subprocess.run(["git", "checkout", "main"]))
            print("Create tag " + f)
        name = f
        break

if name != "":
    print(subprocess.run(["gh", "release", "delete", name, "--yes"]))
    print(subprocess.run(["gh", "release", "create", name, "--verify-tag", "--prerelease", name + "/*/*.msi", name + "/*/*.zip", name + "/*/*.dmg"]))
    print("Create releases")
    shutil.rmtree(name)
