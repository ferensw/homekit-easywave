# Homekit Easywave (cover)
Python Homekit implementation of the Eldat Easywave protocol.<br>
This code is aimed for window covering.<br>
You need to have an Eldat RX09 or RTR09 Easywave USB transmitter to send commands to the cover and to receive commands from the hand remote control.

For info on the Homekit implementation see: https://github.com/ikalchev/HAP-python

Previously I had this implemented through Home Assistant, but since I was only using it for the covers, I decided to make this homekit only version.<br>
It acts as a Homekit gateway with 0..n window covers.<br>
Since it listens to the hand remote control commands, it keeps the status up-to-date.

You can add the config for the covers in config.json

## config.json
"name": "\<Name of the cover>",<br>
"channel_id": "\<Position ID used in the USB transmitter>",<br>
"remote_id": "\<Remote id of the remote control>",<br>
"time_up": \<Time needed for the cover to go from down to up>,<br>
"time_down": \<Time needed for the cover to go from up to down>
  
## Credits
Inspiration from https://github.com/aequitas/python-rflink
