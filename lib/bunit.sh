#!/bin/bash -- just to enable syntax highlighting --
#

##################### ---- UNIT TEST HELPERS ---- #####################

ut0()
{
	# print a heading for a unit test, to stderr

	local STR="$@"
	printf "%-45s : " "$STR" 1>&2
}

ut1()
{
	# print a heading for a group of unit tests, to stderr

	local STR="$@"
	echo "======== $STR ========" 1>&2
}

ut_exit()
{
	if [[ ! -z $_UT_FAIL ]]; then
		echo **** error: some unit tests failed.
		exit -1
	else
		echo all unit tests passed.
		exit 0
	fi
}

ut()
{
	# usage: ut <code_to_evaluate> <op> <expected_result>
	#
	# evals code in $1 and uses $2 to compare it to $3

	eval RES="$($1)"
	if [ "$RES" $2 "$3" ]; then
		echo "\$($1) $2 '$3': ok."
	else
		echo "\$($1) $2 '$3': FAILED (lhs: $RES)."
		_UT_FAIL=1
	fi
}
