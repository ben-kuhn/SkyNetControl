import os
import sys

from backend.modules.checkins.mailbox_reader import read_mailbox, read_message_file

inbox = "/var/lib/pat/.local/share/pat/mailbox/W0NE/in"
net_address = "w0ne@winlink.org"

files = sorted(os.listdir(inbox))
print(f"files on disk: {len(files)}")

matched = read_mailbox(inbox, net_address=net_address)
print(f"parsed+matched: {len(matched)}")

for f in files[:5]:
    p = os.path.join(inbox, f)
    r = read_message_file(p)
    if r:
        print(
            f"  {f}: OK mid={r['message_id']!r} "
            f"from={r['from_address']!r} to={r['to_address']!r}"
        )
    else:
        with open(p) as fh:
            head = fh.readline().rstrip()
        print(f"  {f}: DROPPED first_line={head!r}")
