
# Bumped when the result's shape or a state rule changes.
# 3 (3.0.0 release): two changes since 2.
# - a condition may carry ``polarity: "hazard"``, and a hazard is never counted (#843).
# - owner exclusions add the optional ``excluded`` list and the condition and blocker
#   fields ``disposition`` and ``exclusion_kind`` (#910); results without exclusions are
#   unchanged.
LEVEL_RESULT_VERSION = 3

