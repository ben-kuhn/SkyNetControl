from backend.modules.checkins.message_parser import find_form_xml

FORM_XML = "<RMS_Express_Form><form_parameters><display_form>ICS213.html</display_form>" \
           "<reply_template>ICS213Reply.txt</reply_template></form_parameters>" \
           "<variables><msgbody>hi</msgbody></variables></RMS_Express_Form>"


def test_finds_form_in_attachment():
    atts = [
        {"filename": "photo.jpg", "content_type": "image/jpeg", "data": b"\xff\xd8"},
        {"filename": "RMS_Express_Form_ICS213.xml", "content_type": "application/xml",
         "data": FORM_XML.encode("utf-8")},
    ]
    assert find_form_xml(atts, body="unrelated body") == FORM_XML


def test_finds_form_in_body_fallback():
    assert find_form_xml([], body=f"prose\n{FORM_XML}\nfooter") == FORM_XML


def test_attachment_wins_over_body():
    body_form = FORM_XML.replace("ICS213.html", "BODY.html")
    atts = [{"filename": "RMS_Express_Form_ICS213.xml", "content_type": "application/xml",
             "data": FORM_XML.encode("utf-8")}]
    assert "ICS213.html" in find_form_xml(atts, body=body_form)


def test_none_when_no_form():
    assert find_form_xml([], body="just text") is None
    assert find_form_xml([{"filename": "x.jpg", "content_type": "image/jpeg", "data": b"x"}], body="") is None
