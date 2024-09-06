# MAGLab_Events_Bot
For syncing up events and our open status switch to Discord Events.

Bot Script 1: Takes MAGLab's Open Status Switch (webscraped from: https://www.maglaboratory.org/hal) and updates the Discord Events respectively\
Bot Script 2: Takes MAGLab Calendar + Curator events and updates the Discord events respectively. [Only reports 7 days into the future]\

Benefit 1: People don't need to check hal to see if the space is open. Opening discord is way more natural. Good for general members.\
Benefit 2: No need to check google calendar to see if event or curator stuff is going on.\
Benefit 3: Good reminder to flip the open switch.

If there's an event is cancelled or removed from the google calendar, then it'll remove it from the Discord (at the current specified 1 hour refresh rate).\
If a event is currently active, then the "We are Open/Closed" event will be removed, to let the main event shine.\
The "Open/Closed event" ends 5 minutes into the future (rolling), and webscrapes hal at 1 minute intervals. If we get a power outage, then the event will just disappear in 5 minutes.
