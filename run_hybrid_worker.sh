#!/bin/bash
# dyno number avail in $DYNO as per http://stackoverflow.com/questions/16372425/can-you-programmatically-access-current-heroku-dyno-id-name/16381078#16381078

for (( i=1; i<=8; i++ ))
do
  COMMAND="python update.py Crossref.run_with_hybrid --chunk=1 --limit=100000000 --name=hybrid-$DYNO:${i} "
  echo $COMMAND
  $COMMAND&
done
trap "kill 0" INT TERM EXIT
wait
