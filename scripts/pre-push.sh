#!/usr/bin/env bash
#
# Safety hook: refuses cross-pushes between the two remotes.
#   - answers must NEVER push to public
#   - main must NEVER push to private
#
# Install with:
#   ln -sf ../../scripts/pre-push.sh .git/hooks/pre-push

remote_name="$1"

while read -r local_ref local_sha remote_ref remote_sha; do
  case "${remote_name}:${local_ref}" in
    public:refs/heads/answers)
      echo "BLOCKED: refusing to push 'answers' to 'public' remote." >&2
      echo "         Run scripts/sync-public.sh and push 'main' instead." >&2
      exit 1
      ;;
    private:refs/heads/main)
      echo "BLOCKED: refusing to push 'main' to 'private' remote." >&2
      echo "         Push 'answers' to private; main goes to public." >&2
      exit 1
      ;;
  esac
done

exit 0
