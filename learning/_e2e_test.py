import sys
sys.path.insert(0, "/home/kali/.jarvis")
from learning.rapid_learner import store_interaction, process_feedback
iid = 'test-123'
store_interaction(interaction_id=iid, query='how do I restart the gateway?',
                  response='Use pm2 restart gateway, Sir.', tier='cloud',
                  model='claude', rag_context_ids=[])
print('stored')
print('feedback up:', process_feedback(iid, 'thumbs_up'))
print('feedback down:', process_feedback('test-124', 'thumbs_down',
      correction='Actually use: pm2 restart asthacash-backend'))
import metrics
import json
print('dashboard:', json.dumps(metrics.dashboard(), indent=2))
print('learning-stats counts OK')
