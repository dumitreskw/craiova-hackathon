#!/bin/sh
# Print the build id: the first 8 characters of the commit being built.
# $1: the SOURCE_COMMIT build arg (may be empty); $2: the .git directory.
c="$1"
g="$2"
if [ -z "$c" ] && [ -f "$g/HEAD" ]; then
  h=$(cat "$g/HEAD")
  case "$h" in
    "ref: "*)
      r="${h#ref: }"
      if [ -f "$g/$r" ]; then
        c=$(cat "$g/$r")
      elif [ -f "$g/packed-refs" ]; then
        c=$(grep " $r\$" "$g/packed-refs" | cut -d" " -f1)
      fi ;;
    *) c="$h" ;;
  esac
fi
printf '%s\n' "${c:-dev}" | cut -c1-8
