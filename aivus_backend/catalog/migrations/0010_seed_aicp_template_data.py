from django.db import migrations


CATEGORIES = [
    {"name": "PRODUCTION", "level": 1, "parent": None, "code": "", "tags": ["production"]},
    {"name": "POST-PRODUCTION", "level": 1, "parent": None, "code": "", "tags": ["post_production"]},
    {"name": "Prep Crew", "level": 2, "parent": "PRODUCTION", "code": "A", "tags": ["production"]},
    {"name": "Shoot Crew", "level": 2, "parent": "PRODUCTION", "code": "B", "tags": ["production"]},
    {"name": "Prep & Wrap Expenses", "level": 2, "parent": "PRODUCTION", "code": "C", "tags": ["production"]},
    {"name": "Location Expenses", "level": 2, "parent": "PRODUCTION", "code": "D", "tags": ["production"]},
    {"name": "Props, Wardrobe & Animals", "level": 2, "parent": "PRODUCTION", "code": "E", "tags": ["production"]},
    {"name": "Studio Costs", "level": 2, "parent": "PRODUCTION", "code": "F", "tags": ["production"]},
    {"name": "Art Department Labor", "level": 2, "parent": "PRODUCTION", "code": "G", "tags": ["production"]},
    {"name": "Art Department Expenses", "level": 2, "parent": "PRODUCTION", "code": "H", "tags": ["production"]},
    {"name": "Equipment Rental", "level": 2, "parent": "PRODUCTION", "code": "I", "tags": ["production"]},
    {"name": "Media", "level": 2, "parent": "PRODUCTION", "code": "J", "tags": ["production"]},
    {"name": "Miscellaneous Production Costs", "level": 2, "parent": "PRODUCTION", "code": "K", "tags": ["production"]},
    {"name": "Director's Fees", "level": 2, "parent": "PRODUCTION", "code": "L", "tags": ["production"]},
    {"name": "Talent", "level": 2, "parent": "PRODUCTION", "code": "M", "tags": ["production"]},
    {"name": "Additional Talent Costs", "level": 2, "parent": "PRODUCTION", "code": "M2", "tags": ["production"]},
    {"name": "Talent Expenses", "level": 2, "parent": "PRODUCTION", "code": "N", "tags": ["production"]},
    {"name": "Other", "level": 2, "parent": "PRODUCTION", "code": "O", "tags": ["production"]},
    {"name": "Editorial", "level": 2, "parent": "POST-PRODUCTION", "code": "Q", "tags": ["post_production"]},
    {"name": "Social Versions", "level": 2, "parent": "POST-PRODUCTION", "code": "R", "tags": ["post_production"]},
    {"name": "Audio", "level": 2, "parent": "POST-PRODUCTION", "code": "S", "tags": ["post_production"]},
    {"name": "Finishing", "level": 2, "parent": "POST-PRODUCTION", "code": "T", "tags": ["post_production"]},
    {"name": "Miscellaneous Editorial", "level": 2, "parent": "POST-PRODUCTION", "code": "V", "tags": ["post_production"]},
    {"name": "Editorial Labor & Creative Fees", "level": 2, "parent": "POST-PRODUCTION", "code": "W", "tags": ["post_production"]},
]

ENTRIES = [
    # ── A. Prep Crew ──
    {"code": "1", "name": "Line Producer", "cat": "A", "unit": "Day"},
    {"code": "2", "name": "Assistant Director", "cat": "A", "unit": "Day"},
    {"code": "3", "name": "Director of Photography", "cat": "A", "unit": "Day"},
    {"code": "4", "name": "1st Assistant Camera", "cat": "A", "unit": "Day"},
    {"code": "5", "name": "2nd Assistant Camera", "cat": "A", "unit": "Day"},
    {"code": "6", "name": "DIT", "cat": "A", "unit": "Day"},
    {"code": "7", "name": "Prop Master", "cat": "A", "unit": "Day"},
    {"code": "8", "name": "Asst Props", "cat": "A", "unit": "Day"},
    {"code": "10", "name": "Camera Op", "cat": "A", "unit": "Day"},
    {"code": "11", "name": "Gaffer", "cat": "A", "unit": "Day"},
    {"code": "12", "name": "Best Boy Electric", "cat": "A", "unit": "Day"},
    {"code": "13", "name": "3rd Electric", "cat": "A", "unit": "Day"},
    {"code": "14", "name": "Electric/Driver", "cat": "A", "unit": "Day"},
    {"code": "15", "name": "Prep/Strike/Pre Rig Crew", "cat": "A", "unit": "Day"},
    {"code": "16", "name": "Key Grip", "cat": "A", "unit": "Day"},
    {"code": "17", "name": "Best Boy Grip", "cat": "A", "unit": "Day"},
    {"code": "18", "name": "3rd Grip", "cat": "A", "unit": "Day"},
    {"code": "19", "name": "Grip/Driver", "cat": "A", "unit": "Day"},
    {"code": "20", "name": "Crane Tech 2x", "cat": "A", "unit": "Day"},
    {"code": "21", "name": "Crane Head Tech", "cat": "A", "unit": "Day"},
    {"code": "22", "name": "Steadi Cam Op", "cat": "A", "unit": "Day"},
    {"code": "23", "name": "Choreographer", "cat": "A", "unit": "Day"},
    {"code": "24", "name": "Make-Up/Hair", "cat": "A", "unit": "Day"},
    {"code": "25", "name": "Make-Up/Hair Asst", "cat": "A", "unit": "Day"},
    {"code": "26", "name": "Wardrobe Stylist", "cat": "A", "unit": "Day"},
    {"code": "27", "name": "Asst Wardrobe", "cat": "A", "unit": "Day"},
    {"code": "28", "name": "Script Supervisor", "cat": "A", "unit": "Day"},
    {"code": "29", "name": "Boom Operator", "cat": "A", "unit": "Day"},
    {"code": "30", "name": "Sound Mixer", "cat": "A", "unit": "Day"},
    {"code": "31", "name": "VTR Operator", "cat": "A", "unit": "Day"},
    {"code": "32", "name": "Stunt Coordinator", "cat": "A", "unit": "Day"},
    {"code": "33", "name": "Safety Officer", "cat": "A", "unit": "Day"},
    {"code": "34", "name": "Site Rep", "cat": "A", "unit": "Day"},
    {"code": "35", "name": "Storyboard Artist", "cat": "A", "unit": "Day"},
    {"code": "36", "name": "Catering Crew", "cat": "A", "unit": "Day"},
    {"code": "37", "name": "Location Scout", "cat": "A", "unit": "Day"},
    {"code": "38", "name": "Compliance Assistant", "cat": "A", "unit": "Day"},
    {"code": "39", "name": "2nd AD", "cat": "A", "unit": "Day"},
    {"code": "40", "name": "Medic", "cat": "A", "unit": "Day"},
    {"code": "41", "name": "Craft Service", "cat": "A", "unit": "Day"},
    {"code": "42", "name": "Firefighter", "cat": "A", "unit": "Day"},
    {"code": "43", "name": "Police Officers/Ranger/CHP", "cat": "A", "unit": "Day"},
    {"code": "44", "name": "Welfare/Teacher", "cat": "A", "unit": "Day"},
    {"code": "45", "name": "Gang Boss", "cat": "A", "unit": "Day"},
    {"code": "46", "name": "Teamster Drivers / Animal Wranglers", "cat": "A", "unit": "Day"},
    {"code": "47", "name": "Production Supervisor", "cat": "A", "unit": "Day"},
    {"code": "48", "name": "Assistant Production Supervisor", "cat": "A", "unit": "Day"},
    {"code": "49", "name": "Production Assistant", "cat": "A", "unit": "Day"},

    # ── B. Shoot Crew ──
    {"code": "51", "name": "Line Producer", "cat": "B", "unit": "Day"},
    {"code": "52", "name": "Assistant Director", "cat": "B", "unit": "Day"},
    {"code": "53", "name": "Director of Photography", "cat": "B", "unit": "Day"},
    {"code": "54", "name": "1st Assistant Camera", "cat": "B", "unit": "Day"},
    {"code": "55", "name": "2nd Assistant Camera", "cat": "B", "unit": "Day"},
    {"code": "56", "name": "DIT", "cat": "B", "unit": "Day"},
    {"code": "57", "name": "Prop Master", "cat": "B", "unit": "Day"},
    {"code": "58", "name": "Asst Props", "cat": "B", "unit": "Day"},
    {"code": "60", "name": "Camera Op", "cat": "B", "unit": "Day"},
    {"code": "61", "name": "Gaffer", "cat": "B", "unit": "Day"},
    {"code": "62", "name": "Best Boy Electric", "cat": "B", "unit": "Day"},
    {"code": "63", "name": "3rd Electric", "cat": "B", "unit": "Day"},
    {"code": "64", "name": "Electric/Driver", "cat": "B", "unit": "Day"},
    {"code": "65", "name": "Prep/Strike/Pre Rig Crew", "cat": "B", "unit": "Day"},
    {"code": "66", "name": "Key Grip", "cat": "B", "unit": "Day"},
    {"code": "67", "name": "Best Boy Grip", "cat": "B", "unit": "Day"},
    {"code": "68", "name": "3rd Grip", "cat": "B", "unit": "Day"},
    {"code": "69", "name": "Grip/Driver", "cat": "B", "unit": "Day"},
    {"code": "70", "name": "Crane Tech 2x", "cat": "B", "unit": "Day"},
    {"code": "71", "name": "Crane Head Tech", "cat": "B", "unit": "Day"},
    {"code": "72", "name": "Steadi Cam Op", "cat": "B", "unit": "Day"},
    {"code": "73", "name": "Choreographer", "cat": "B", "unit": "Day"},
    {"code": "74", "name": "Make-Up/Hair", "cat": "B", "unit": "Day"},
    {"code": "75", "name": "Make-Up/Hair Asst", "cat": "B", "unit": "Day"},
    {"code": "76", "name": "Wardrobe Stylist", "cat": "B", "unit": "Day"},
    {"code": "77", "name": "Asst Wardrobe", "cat": "B", "unit": "Day"},
    {"code": "78", "name": "Script Supervisor", "cat": "B", "unit": "Day"},
    {"code": "79", "name": "Boom Operator", "cat": "B", "unit": "Day"},
    {"code": "80", "name": "Sound Mixer", "cat": "B", "unit": "Day"},
    {"code": "81", "name": "VTR Operator", "cat": "B", "unit": "Day"},
    {"code": "82", "name": "Stunt Coordinator", "cat": "B", "unit": "Day"},
    {"code": "83", "name": "Safety Officer", "cat": "B", "unit": "Day"},
    {"code": "84", "name": "Site Rep", "cat": "B", "unit": "Day"},
    {"code": "85", "name": "Storyboard Artist", "cat": "B", "unit": "Day"},
    {"code": "86", "name": "Catering Crew", "cat": "B", "unit": "Day"},
    {"code": "87", "name": "Location Manager", "cat": "B", "unit": "Day"},
    {"code": "88", "name": "Compliance Assistant", "cat": "B", "unit": "Day"},
    {"code": "89", "name": "2nd AD", "cat": "B", "unit": "Day"},
    {"code": "90", "name": "Medic", "cat": "B", "unit": "Day"},
    {"code": "91", "name": "Craft Service", "cat": "B", "unit": "Day"},
    {"code": "92", "name": "Firefighter", "cat": "B", "unit": "Day"},
    {"code": "93", "name": "Police Officers/Ranger/CHP", "cat": "B", "unit": "Day"},
    {"code": "94", "name": "Welfare/Teacher", "cat": "B", "unit": "Day"},
    {"code": "95", "name": "Gang Boss", "cat": "B", "unit": "Day"},
    {"code": "96", "name": "Teamster Drivers / Animal Wranglers", "cat": "B", "unit": "Day"},
    {"code": "97", "name": "Production Supervisor", "cat": "B", "unit": "Day"},
    {"code": "98", "name": "Assistant Production Supervisor", "cat": "B", "unit": "Day"},
    {"code": "99", "name": "Production Assistant", "cat": "B", "unit": "Day"},

    # ── C. Prep & Wrap Expenses ──
    {"code": "101", "name": "Craft Service", "cat": "C", "unit": "Each"},
    {"code": "102", "name": "Per Diems", "cat": "C", "unit": "Each"},
    {"code": "103", "name": "Hotels", "cat": "C", "unit": "Each"},
    {"code": "104", "name": "Scouting Expenses", "cat": "C", "unit": "Each"},
    {"code": "105", "name": "Deliveries & Taxi", "cat": "C", "unit": "Flat"},
    {"code": "106", "name": "Car Rental", "cat": "C", "unit": "Each"},
    {"code": "107", "name": "Trucking", "cat": "C", "unit": "Each"},
    {"code": "108", "name": "Casting Director", "cat": "C", "unit": "Day"},
    {"code": "109", "name": "Casting Facility", "cat": "C", "unit": "Day"},
    {"code": "110", "name": "Home Econ Supplies", "cat": "C", "unit": "Each"},
    {"code": "111", "name": "Telephone & Cable", "cat": "C", "unit": "Each"},
    {"code": "112", "name": "Working Meals", "cat": "C", "unit": "Each"},
    {"code": "113", "name": "Messengers", "cat": "C", "unit": "Each"},

    # ── D. Location Expenses ──
    {"code": "114", "name": "Location Fees", "cat": "D", "unit": "Each"},
    {"code": "115", "name": "Permits", "cat": "D", "unit": "Each"},
    {"code": "116", "name": "Lane Closures", "cat": "D", "unit": "Each"},
    {"code": "117", "name": "Set Security", "cat": "D", "unit": "Each"},
    {"code": "118", "name": "Cargo Van", "cat": "D", "unit": "Each"},
    {"code": "119", "name": "Production Trucking", "cat": "D", "unit": "Each"},
    {"code": "120", "name": "Camera Truck", "cat": "D", "unit": "Each"},
    {"code": "121", "name": "Car Rentals", "cat": "D", "unit": "Each"},
    {"code": "122", "name": "Bus Rentals", "cat": "D", "unit": "Each"},
    {"code": "123", "name": "Limousines", "cat": "D", "unit": "Each"},
    {"code": "124", "name": "Dressing Room Vehicles", "cat": "D", "unit": "Each"},
    {"code": "125", "name": "Production MoHo", "cat": "D", "unit": "Each"},
    {"code": "126", "name": "Other Vehicles", "cat": "D", "unit": "Each"},
    {"code": "127", "name": "Parking/Tolls/Gas", "cat": "D", "unit": "Each"},
    {"code": "128", "name": "Excess Bags/Homeland Security", "cat": "D", "unit": "Each"},
    {"code": "129", "name": "Air Fares", "cat": "D", "unit": "Each"},
    {"code": "130", "name": "Hotels", "cat": "D", "unit": "Each"},
    {"code": "131", "name": "Per Diems", "cat": "D", "unit": "Each"},
    {"code": "132", "name": "Talent Meals", "cat": "D", "unit": "Each"},
    {"code": "133", "name": "Breakfast", "cat": "D", "unit": "Person"},
    {"code": "134", "name": "Lunch", "cat": "D", "unit": "Person"},
    {"code": "135", "name": "Dinner", "cat": "D", "unit": "Person"},
    {"code": "136", "name": "Cabs/Ubers/Lyfts/Other Transportation", "cat": "D", "unit": "Each"},
    {"code": "137", "name": "Kit Rental", "cat": "D", "unit": "Each"},
    {"code": "138", "name": "Art Work", "cat": "D", "unit": "Each"},
    {"code": "139", "name": "Sustainable Practices", "cat": "D", "unit": "Flat"},

    # ── E. Props, Wardrobe & Animals ──
    {"code": "140", "name": "Prop Rental", "cat": "E", "unit": "Each"},
    {"code": "141", "name": "Prop Purchase", "cat": "E", "unit": "Each"},
    {"code": "142", "name": "Prop Fabrication", "cat": "E", "unit": "Each"},
    {"code": "143", "name": "Wardrobe Rental", "cat": "E", "unit": "Each"},
    {"code": "144", "name": "Wardrobe Purchase", "cat": "E", "unit": "Each"},
    {"code": "145", "name": "Costumes", "cat": "E", "unit": "Each"},
    {"code": "146", "name": "Picture Vehicles", "cat": "E", "unit": "Each"},
    {"code": "147", "name": "Animals & Handlers", "cat": "E", "unit": "Each"},
    {"code": "148", "name": "Theatrical Makeup", "cat": "E", "unit": "Each"},
    {"code": "149", "name": "Product Prep / Color Correct", "cat": "E", "unit": "Each"},
    {"code": "150", "name": "Greens", "cat": "E", "unit": "Each"},

    # ── F. Studio Costs ──
    {"code": "151", "name": "Rental For Build Days", "cat": "F", "unit": "Day"},
    {"code": "152", "name": "Build OT Hours", "cat": "F", "unit": "Hour"},
    {"code": "153", "name": "Rental for Pre-Lite Days", "cat": "F", "unit": "Day"},
    {"code": "154", "name": "Pre-Lite OT Hours", "cat": "F", "unit": "Day"},
    {"code": "155", "name": "Rental for Shoot Days", "cat": "F", "unit": "Day"},
    {"code": "156", "name": "Shoot OT Hours", "cat": "F", "unit": "Hour"},
    {"code": "157", "name": "Rental for Strike Days", "cat": "F", "unit": "Day"},
    {"code": "158", "name": "Strike OT Hours", "cat": "F", "unit": "Hour"},
    {"code": "159", "name": "Generator and Operator", "cat": "F", "unit": "Day"},
    {"code": "160", "name": "Stage Manager/Studio Security", "cat": "F", "unit": "Day"},
    {"code": "161", "name": "Power Charges", "cat": "F", "unit": "Day"},
    {"code": "162", "name": "Misc Studio Charges", "cat": "F", "unit": "Day"},
    {"code": "163", "name": "Meals for Crew & Talent", "cat": "F", "unit": "Day"},
    {"code": "164", "name": "Air Conditioning", "cat": "F", "unit": "Day"},
    {"code": "165", "name": "Crew Parking", "cat": "F", "unit": "Day"},
    {"code": "166", "name": "Condor/Scissor Lift", "cat": "F", "unit": "Day"},
    {"code": "167", "name": "Steeldeck", "cat": "F", "unit": "Day"},

    # ── G. Art Department Labor ──
    {"code": "168", "name": "Production Designer/Art Director", "cat": "G", "unit": "Day"},
    {"code": "170", "name": "Set Decorator", "cat": "G", "unit": "Day"},
    {"code": "171", "name": "Art Dept Coordinator", "cat": "G", "unit": "Day"},
    {"code": "172", "name": "Prop Master", "cat": "G", "unit": "Day"},
    {"code": "173", "name": "Asst Props", "cat": "G", "unit": "Day"},
    {"code": "174", "name": "Swing", "cat": "G", "unit": "Day"},
    {"code": "175", "name": "Leadman", "cat": "G", "unit": "Day"},
    {"code": "176", "name": "Set Dresser", "cat": "G", "unit": "Day"},
    {"code": "177", "name": "Scenics", "cat": "G", "unit": "Day"},
    {"code": "178", "name": "Grips / Riggers", "cat": "G", "unit": "Day"},

    # ── H. Art Department Expenses ──
    {"code": "181", "name": "Set Dressing Rentals", "cat": "H", "unit": "Each"},
    {"code": "182", "name": "Set Dressing Purchases", "cat": "H", "unit": "Each"},
    {"code": "183", "name": "Art Dept Prod Supplies", "cat": "H", "unit": "Each"},
    {"code": "184", "name": "Art Dept Kit Rental", "cat": "H", "unit": "Each"},
    {"code": "185", "name": "Special Effects Rental", "cat": "H", "unit": "Each"},
    {"code": "186", "name": "Art Dept Trucking", "cat": "H", "unit": "Each"},
    {"code": "187", "name": "Outside Construction", "cat": "H", "unit": "Each"},
    {"code": "188", "name": "Car Prep", "cat": "H", "unit": "Each"},
    {"code": "189", "name": "Art Dept Meals", "cat": "H", "unit": "Each"},
    {"code": "190", "name": "Messengers/Deliveries", "cat": "H", "unit": "Each"},

    # ── I. Equipment Rental ──
    {"code": "193", "name": "Camera Rental", "cat": "I", "unit": "Day"},
    {"code": "194", "name": "Sound Rental", "cat": "I", "unit": "Day"},
    {"code": "195", "name": "Lighting Rental", "cat": "I", "unit": "Day"},
    {"code": "196", "name": "Grip Rental", "cat": "I", "unit": "Day"},
    {"code": "197", "name": "Generator Rental", "cat": "I", "unit": "Day"},
    {"code": "198", "name": "Crane Rental", "cat": "I", "unit": "Day"},
    {"code": "199", "name": "VTR Rental", "cat": "I", "unit": "Day"},
    {"code": "200", "name": "Walkie Talkie Rental", "cat": "I", "unit": "Day"},
    {"code": "201", "name": "Dolly Rental", "cat": "I", "unit": "Day"},
    {"code": "202", "name": "SteadiCam", "cat": "I", "unit": "Day"},
    {"code": "203", "name": "Helicopter", "cat": "I", "unit": "Day"},
    {"code": "204", "name": "Production Supplies", "cat": "I", "unit": "Day"},
    {"code": "205", "name": "Jib Arm", "cat": "I", "unit": "Day"},
    {"code": "206", "name": "Crane Head", "cat": "I", "unit": "Day"},
    {"code": "207", "name": "Camera Car", "cat": "I", "unit": "Day"},
    {"code": "208", "name": "Expendables", "cat": "I", "unit": "Day"},
    {"code": "209", "name": "Lenses", "cat": "I", "unit": "Day"},
    {"code": "210", "name": "Cinedrives", "cat": "I", "unit": "Day"},

    # ── J. Media ──
    {"code": "211", "name": "Media / Drives", "cat": "J", "unit": "Each"},
    {"code": "212", "name": "Film", "cat": "J", "unit": "Each"},
    {"code": "213", "name": "Transcode / Transfer", "cat": "J", "unit": "Hour"},
    {"code": "214", "name": "Process", "cat": "J", "unit": "Hour"},
    {"code": "215", "name": "Dailies", "cat": "J", "unit": "Each"},

    # ── K. Miscellaneous Production Costs ──
    {"code": "217", "name": "Petty Cash", "cat": "K", "unit": "Each"},
    {"code": "218", "name": "Air Shipping and Carriers", "cat": "K", "unit": "Each"},
    {"code": "219", "name": "Phones and Cables", "cat": "K", "unit": "Each"},
    {"code": "220", "name": "Cash Under $15 Each", "cat": "K", "unit": "Each"},
    {"code": "221", "name": "External Billing Costs", "cat": "K", "unit": "Each"},
    {"code": "222", "name": "Special Insurance", "cat": "K", "unit": "Each"},
    {"code": "223", "name": "Cell Phones", "cat": "K", "unit": "Each"},
    {"code": "224", "name": "Foreign Production Service Co", "cat": "K", "unit": "Each"},

    # ── L. Director's Fees ──
    {"code": "227", "name": "Director Prep", "cat": "L", "unit": "Day"},
    {"code": "228", "name": "Director Travel", "cat": "L", "unit": "Day"},
    {"code": "229", "name": "Director Shoot", "cat": "L", "unit": "Day"},
    {"code": "230", "name": "Director Post", "cat": "L", "unit": "Day"},
    {"code": "231", "name": "Fringes", "cat": "L", "unit": "Day"},

    # ── M. Talent ──
    {"code": "234", "name": "O/C Principals", "cat": "M", "unit": "Day"},
    {"code": "244", "name": "Office Extras", "cat": "M", "unit": "Day"},
    {"code": "246", "name": "Crowd Extras", "cat": "M", "unit": "Day"},
    {"code": "247", "name": "General Extras", "cat": "M", "unit": "Day"},
    {"code": "255", "name": "Hand Models", "cat": "M", "unit": "Day"},
    {"code": "258", "name": "Voice Over", "cat": "M", "unit": "Day"},
    {"code": "259", "name": "Fitting Fees", "cat": "M", "unit": "Day"},
    {"code": "262", "name": "Audition Fees", "cat": "M", "unit": "Day"},

    # ── M2. Additional Talent Costs ──
    {"code": "266", "name": "Talent Agency Fees", "cat": "M2", "unit": "Each"},
    {"code": "267", "name": "Talent Payroll Service", "cat": "M2", "unit": "Each"},
    {"code": "268", "name": "Talent Wardrobe Allowance", "cat": "M2", "unit": "Each"},

    # ── N. Talent Expenses ──
    {"code": "271", "name": "Talent Air Fares", "cat": "N", "unit": "Each"},
    {"code": "272", "name": "Talent Per Diem", "cat": "N", "unit": "Each"},
    {"code": "273", "name": "Talent Gd Transportation", "cat": "N", "unit": "Each"},

    # ── O. Other ── (all rows empty, no entries)

    # ── Q. Editorial ──
    {"code": "2010", "name": "File Conversion & Transcoding", "cat": "Q", "unit": "Hour"},
    {"code": "2020", "name": "Breakdown", "cat": "Q", "unit": "Hour"},
    {"code": "2030", "name": "Stock Footage Search", "cat": "Q", "unit": "Hour"},
    {"code": "2040", "name": "Digital Dailies Transfer", "cat": "Q", "unit": "Hour"},
    {"code": "2050", "name": "Transcription / Translation", "cat": "Q", "unit": "Flat"},
    {"code": "2110", "name": "Offline Edit System", "cat": "Q", "unit": "Day"},
    {"code": "2120", "name": "Off-Line Graphics System", "cat": "Q", "unit": "Day"},
    {"code": "2130", "name": "Data Backup / Restore", "cat": "Q", "unit": "Flat"},
    {"code": "2140", "name": "Conform", "cat": "Q", "unit": "Hour"},
    {"code": "2150", "name": "Hi-Res Conform", "cat": "Q", "unit": "Hour"},
    {"code": "2210", "name": "Mix Prep", "cat": "Q", "unit": "Flat"},
    {"code": "2220", "name": "Color Prep", "cat": "Q", "unit": "Flat"},
    {"code": "2230", "name": "Conform Prep", "cat": "Q", "unit": "Flat"},
    {"code": "2240", "name": "Graphics Prep", "cat": "Q", "unit": "Flat"},
    {"code": "2310", "name": "Remote Off-Line Edit Suite", "cat": "Q", "unit": "Day"},
    {"code": "2320", "name": "Digital Media", "cat": "Q", "unit": "Flat"},
    {"code": "2330", "name": "Offline Posting", "cat": "Q", "unit": "Flat"},
    {"code": "2340", "name": "Backup / Restore", "cat": "Q", "unit": "Flat"},
    {"code": "2350", "name": "Archiving", "cat": "Q", "unit": "Flat"},

    # ── R. Social Versions ──
    {"code": "3010", "name": "Additional Cleanup", "cat": "R", "unit": "Hour"},
    {"code": "3020", "name": "Re-position / Re-composite", "cat": "R", "unit": "Hour"},
    {"code": "3030", "name": "Re-animate", "cat": "R", "unit": "Hour"},
    {"code": "3040", "name": "Frame Extension", "cat": "R", "unit": "Hour"},
    {"code": "3050", "name": "Pre-Roll Versions", "cat": "R", "unit": "Flat"},
    {"code": "3110", "name": "Additional Grading", "cat": "R", "unit": "Hour"},
    {"code": "3120", "name": "File Versioning / Compression", "cat": "R", "unit": "Hour"},
    {"code": "3130", "name": "Reformatting 1 x 1", "cat": "R", "unit": "Hour"},
    {"code": "3140", "name": "Reformatting 9 x 16", "cat": "R", "unit": "Hour"},
    {"code": "3150", "name": "Reformatting 4 x 3", "cat": "R", "unit": "Hour"},
    {"code": "3160", "name": "Reformatting 5 x 4", "cat": "R", "unit": "Hour"},
    {"code": "3210", "name": "Reframing 1 x 1", "cat": "R", "unit": "Hour"},
    {"code": "3220", "name": "Reframing 9 x 16", "cat": "R", "unit": "Hour"},
    {"code": "3230", "name": "Reframing 4 x 3", "cat": "R", "unit": "Hour"},
    {"code": "3240", "name": "Reframing 5 x 4", "cat": "R", "unit": "Hour"},
    {"code": "3310", "name": "Social Mixes", "cat": "R", "unit": "Hour"},
    {"code": "3320", "name": "Social Music Edits", "cat": "R", "unit": "Hour"},
    {"code": "3330", "name": "Additional VO Record", "cat": "R", "unit": "Hour"},
    {"code": "3410", "name": "Additional Drives", "cat": "R", "unit": "Each"},
    {"code": "3420", "name": "Postings / Digital Delivery / QC", "cat": "R", "unit": "Flat"},

    # ── S. Audio ──
    {"code": "4010", "name": "Pre-Load, Encode and Mix Prep", "cat": "S", "unit": "Hour"},
    {"code": "4020", "name": "Sound Effects / Music Search", "cat": "S", "unit": "Hour"},
    {"code": "4030", "name": "Voice Casting", "cat": "S", "unit": "Hour"},
    {"code": "4040", "name": "Transcription / Translation", "cat": "S", "unit": "Hour"},
    {"code": "4110", "name": "VO Record", "cat": "S", "unit": "Hour"},
    {"code": "4120", "name": "ADR", "cat": "S", "unit": "Hour"},
    {"code": "4130", "name": "5.1 Mix", "cat": "S", "unit": "Hour"},
    {"code": "4140", "name": "Other Format Mixing", "cat": "S", "unit": "Hour"},
    {"code": "4150", "name": "Record and Mix", "cat": "S", "unit": "Hour"},
    {"code": "4160", "name": "Scratch Record", "cat": "S", "unit": "Hour"},
    {"code": "4170", "name": "Record and Mix - Overtime", "cat": "S", "unit": "Hour"},
    {"code": "4210", "name": "Music Licensing (Stock/Original)", "cat": "S", "unit": "Flat"},
    {"code": "4220", "name": "Sound Effects", "cat": "S", "unit": "Hour"},
    {"code": "4230", "name": "Sound Design", "cat": "S", "unit": "Flat"},
    {"code": "4240", "name": "Digital Edit", "cat": "S", "unit": "Hour"},
    {"code": "4310", "name": "Remote Studio Costs", "cat": "S", "unit": "Hour"},
    {"code": "4320", "name": "Digital Patch: ISDN", "cat": "S", "unit": "Hour"},
    {"code": "4330", "name": "Digital Patch: ISDN INT'L", "cat": "S", "unit": "Hour"},
    {"code": "4340", "name": "Digital Patch: Source Connect", "cat": "S", "unit": "Hour"},
    {"code": "4350", "name": "Digital Patch: Skype / Phone", "cat": "S", "unit": "Hour"},
    {"code": "4360", "name": "Field Recording", "cat": "S", "unit": "Day"},
    {"code": "4410", "name": "Media", "cat": "S", "unit": "Flat"},
    {"code": "4420", "name": "Digital File Creation", "cat": "S", "unit": "Flat"},
    {"code": "4430", "name": "Uploads & Machine Room", "cat": "S", "unit": "Flat"},
    {"code": "4440", "name": "Archive", "cat": "S", "unit": "Flat"},
    {"code": "4450", "name": "Audio Relay", "cat": "S", "unit": "Each"},
    {"code": "4460", "name": "Facility Overtime", "cat": "S", "unit": "Hour"},
    {"code": "4470", "name": "Weekend Key Fee", "cat": "S", "unit": "Day"},
    {"code": "4480", "name": "Transfer & Stock", "cat": "S", "unit": "Flat"},
    {"code": "4510", "name": "Deliveries & Messengers", "cat": "S", "unit": "Flat"},
    {"code": "4520", "name": "Shipping", "cat": "S", "unit": "Flat"},
    {"code": "4530", "name": "Inventory/Packing", "cat": "S", "unit": "Flat"},
    {"code": "4540", "name": "Shipping to Storage", "cat": "S", "unit": "Flat"},

    # ── T. Finishing ──
    {"code": "5010", "name": "Color Grading Prep", "cat": "T", "unit": "Hour"},
    {"code": "5020", "name": "Color Grading", "cat": "T", "unit": "Hour"},
    {"code": "5030", "name": "Pre Load/Scanning", "cat": "T", "unit": "Each"},
    {"code": "5040", "name": "Data I/O", "cat": "T", "unit": "Each"},
    {"code": "5050", "name": "Transfers", "cat": "T", "unit": "Each"},
    {"code": "5060", "name": "Remote Set Up", "cat": "T", "unit": "Each"},
    {"code": "5070", "name": "Remote Room", "cat": "T", "unit": "Each"},
    {"code": "5080", "name": "Additional Machines", "cat": "T", "unit": "Each"},
    {"code": "5110", "name": "Final Conform", "cat": "T", "unit": "Hour"},
    {"code": "5120", "name": "Compositing / VFX", "cat": "T", "unit": "Each"},
    {"code": "5130", "name": "Flame Assistant - Roto", "cat": "T", "unit": "Each"},
    {"code": "5140", "name": "2D GFX / Design", "cat": "T", "unit": "Each"},
    {"code": "5150", "name": "Motion Graphics", "cat": "T", "unit": "Hour"},
    {"code": "5160", "name": "Color Correction", "cat": "T", "unit": "Hour"},
    {"code": "5170", "name": "3D Animation", "cat": "T", "unit": "Each"},
    {"code": "5180", "name": "3D Modeling", "cat": "T", "unit": "Each"},
    {"code": "5210", "name": "Archiving", "cat": "T", "unit": "Each"},
    {"code": "5220", "name": "Uncompressed Files", "cat": "T", "unit": "Each"},
    {"code": "5230", "name": "Retouching", "cat": "T", "unit": "Each"},
    {"code": "5240", "name": "Standards Conversions", "cat": "T", "unit": "Each"},
    {"code": "5320", "name": "Drives / Media", "cat": "T", "unit": "Each"},
    {"code": "5330", "name": "Generic Master", "cat": "T", "unit": "Each"},
    {"code": "5340", "name": "Master", "cat": "T", "unit": "Each"},
    {"code": "5350", "name": "Deliverables", "cat": "T", "unit": "Each"},
    {"code": "5360", "name": "Additional Outputs", "cat": "T", "unit": "Each"},
    {"code": "5370", "name": "Archiving Storage Device", "cat": "T", "unit": "Each"},
    {"code": "5380", "name": "Compressed File Dubs", "cat": "T", "unit": "Each"},
    {"code": "5390", "name": "Postings", "cat": "T", "unit": "Each"},
    {"code": "5410", "name": "Deliveries & Messengers", "cat": "T", "unit": "Day"},
    {"code": "5420", "name": "Shipping", "cat": "T", "unit": "Day"},
    {"code": "5430", "name": "Inventory/Packing", "cat": "T", "unit": "Day"},
    {"code": "5440", "name": "Shipping to Storage", "cat": "T", "unit": "Day"},

    # ── V. Miscellaneous Editorial ──
    {"code": "7010", "name": "Storage Devices", "cat": "V", "unit": "Each"},
    {"code": "7020", "name": "Archiving/LTO", "cat": "V", "unit": "Flat"},
    {"code": "7030", "name": "Archive Storage Devices", "cat": "V", "unit": "Each"},
    {"code": "7040", "name": "Tape-to-Film Transfer", "cat": "V", "unit": "Hour"},
    {"code": "7050", "name": "Standards Conversion", "cat": "V", "unit": "Hour"},
    {"code": "7060", "name": "Stock Footage", "cat": "V", "unit": "Flat"},
    {"code": "7070", "name": "Satellite/Digital Transmission", "cat": "V", "unit": "Hour"},
    {"code": "7080", "name": "Data Transmission Charge", "cat": "V", "unit": "Flat"},
    {"code": "7110", "name": "Deliveries & Messengers", "cat": "V", "unit": "Flat"},
    {"code": "7120", "name": "Shipping", "cat": "V", "unit": "Flat"},
    {"code": "7130", "name": "Inventory/Packing", "cat": "V", "unit": "Flat"},
    {"code": "7140", "name": "Shipping to Storage", "cat": "V", "unit": "Flat"},
    {"code": "7150", "name": "Additional Machines", "cat": "V", "unit": "Flat"},
    {"code": "7210", "name": "Airfare", "cat": "V", "unit": "Each"},
    {"code": "7220", "name": "Hotel", "cat": "V", "unit": "Each"},
    {"code": "7230", "name": "Per Diem", "cat": "V", "unit": "Day"},
    {"code": "7240", "name": "Transportation", "cat": "V", "unit": "Flat"},
    {"code": "7250", "name": "Assistant Editor Travel", "cat": "V", "unit": "Flat"},
    {"code": "7310", "name": "Editorial Supplies", "cat": "V", "unit": "Flat"},
    {"code": "7320", "name": "Equipment Rental", "cat": "V", "unit": "Flat"},
    {"code": "7330", "name": "Working Meals", "cat": "V", "unit": "Flat"},
    {"code": "7340", "name": "Weekend Fee", "cat": "V", "unit": "Flat"},

    # ── W. Editorial Labor & Creative Fees ──
    {"code": "8010", "name": "Pre-Production Labor", "cat": "W", "unit": "Day"},
    {"code": "8020", "name": "Editor Labor", "cat": "W", "unit": "Day"},
    {"code": "8030", "name": "Editor OT/Weekend", "cat": "W", "unit": "Day"},
    {"code": "8040", "name": "Assistant Labor", "cat": "W", "unit": "Day"},
    {"code": "8050", "name": "Assistant OT/Weekend", "cat": "W", "unit": "Day"},
    {"code": "8060", "name": "Session Supervisory Fee", "cat": "W", "unit": "Day"},
    {"code": "8070", "name": "Producer/Coordinator", "cat": "W", "unit": "Day"},
    {"code": "8080", "name": "Set Supervision", "cat": "W", "unit": "Day"},
    {"code": "8100", "name": "Creative Fees", "cat": "W", "unit": "Flat"},
]

OLD_CATEGORIES = [
    {"name": "Creative DEVELOPMENT", "level": 1, "parent": None},
    {"name": "PRE-PRODUCTION", "level": 1, "parent": None},
    {"name": "PRODUCTION", "level": 1, "parent": None},
    {"name": "Equipment", "level": 2, "parent": "PRODUCTION"},
    {"name": "Vehicles", "level": 2, "parent": "PRODUCTION"},
    {"name": "Team", "level": 2, "parent": "PRODUCTION"},
]

OLD_UNITS = [
    {"key": 1, "name": "Each", "symbol": "ea", "dimension": "QUANTITY"},
    {"key": 2, "name": "Person", "symbol": "pers", "dimension": "QUANTITY"},
    {"key": 3, "name": "Flat", "symbol": "flat", "dimension": "QUANTITY"},
    {"key": 4, "name": "Set", "symbol": "set", "dimension": "QUANTITY"},
    {"key": 5, "name": "Frame(s)", "symbol": "frm", "dimension": "QUANTITY"},
    {"key": 6, "name": "Location", "symbol": "loc", "dimension": "QUANTITY"},
    {"key": 7, "name": "Model", "symbol": "mdl", "dimension": "QUANTITY"},
    {"key": 8, "name": "Pc(s)", "symbol": "pc", "dimension": "QUANTITY"},
    {"key": 9, "name": "Concept", "symbol": "cpt", "dimension": "QUANTITY"},
    {"key": 10, "name": "Hour", "symbol": "h", "dimension": "TEMPORAL"},
    {"key": 11, "name": "Day", "symbol": "d", "dimension": "TEMPORAL"},
    {"key": 12, "name": "Sec", "symbol": "s", "dimension": "TEMPORAL"},
    {"key": 13, "name": "Min", "symbol": "min", "dimension": "TEMPORAL"},
]

OLD_ENTRIES = [
    {"name": "Concept Development", "category": "Creative DEVELOPMENT", "units": [9, 3]},
    {"name": "KV Development", "category": "Creative DEVELOPMENT", "units": [1, 3]},
    {"name": "Scriptwriting", "category": "Creative DEVELOPMENT", "units": [1, 3, 13]},
    {"name": "Storyboard", "category": "Creative DEVELOPMENT", "units": [5, 3, 13]},
    {"name": "Animatic", "category": "Creative DEVELOPMENT", "units": [1, 3, 12, 13]},
    {"name": "CAST TALENT", "category": "PRE-PRODUCTION", "units": [7, 3]},
    {"name": "SCOUT LOCATIONS", "category": "PRE-PRODUCTION", "units": [6, 3, 11]},
    {"name": "GEAR PREP DAY", "category": "PRE-PRODUCTION", "units": [3, 11]},
    {"name": "Director's Treatment", "category": "PRE-PRODUCTION", "units": [1, 3]},
    {"name": "Camera", "category": "Equipment", "units": [4, 8, 11]},
    {"name": "Lenses", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Monitors", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Additional Camera Accessories", "category": "Equipment", "units": [4, 11]},
    {"name": "Drones", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Dolly", "category": "Equipment", "units": [4, 11]},
    {"name": "Cranes & Jibs", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Stabilizers and Gimbals", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Sliders", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Motion control system", "category": "Equipment", "units": [4, 11]},
    {"name": "Lighting", "category": "Equipment", "units": [4, 11]},
    {"name": "Electric Generators", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Sound Recording Equipment", "category": "Equipment", "units": [4, 11]},
    {"name": "Teleprompter", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Walkie Talkie", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Camera Truck", "category": "Vehicles", "units": [1, 11]},
    {"name": "Grip/Lighting Truck", "category": "Vehicles", "units": [1, 11]},
    {"name": "Makeup Trailer", "category": "Vehicles", "units": [1, 11]},
    {"name": "Wardrobe Trailer", "category": "Vehicles", "units": [1, 11]},
    {"name": "Talent Trailer", "category": "Vehicles", "units": [1, 11]},
    {"name": "Catering Truck", "category": "Vehicles", "units": [1, 11]},
    {"name": "Production Office Trailer", "category": "Vehicles", "units": [1, 11]},
    {"name": "Tech Truck", "category": "Vehicles", "units": [1, 11]},
    {"name": "Props Truck", "category": "Vehicles", "units": [1, 11]},
    {"name": "Portable Toilet", "category": "Vehicles", "units": [1, 11]},
    {"name": "Creative Director", "category": "Team", "units": [2, 10, 11]},
    {"name": "Art Director", "category": "Team", "units": [2, 10, 11]},
    {"name": "Director", "category": "Team", "units": [2, 10, 11]},
    {"name": "Director's Assistant", "category": "Team", "units": [2, 10, 11]},
    {"name": "On-Set Editor", "category": "Team", "units": [2, 10, 11]},
    {"name": "DP / Cinematographer", "category": "Team", "units": [2, 10, 11]},
    {"name": "Focus puller", "category": "Team", "units": [2, 10, 11]},
    {"name": "Camera Assistant (1st AC)", "category": "Team", "units": [2, 10, 11]},
    {"name": "Extra Camera Operator", "category": "Team", "units": [2, 10, 11]},
    {"name": "Camera Tech", "category": "Team", "units": [2, 10, 11]},
    {"name": "Aerial Cinematographer", "category": "Team", "units": [2, 10, 11]},
    {"name": "Gaffer", "category": "Team", "units": [2, 10, 11]},
    {"name": "Grip", "category": "Team", "units": [2, 10, 11]},
    {"name": "Field/Audio Recorder", "category": "Team", "units": [2, 10, 11]},
    {"name": "Photographer", "category": "Team", "units": [2, 10, 11]},
    {"name": "Producer", "category": "Team", "units": [2, 10, 11]},
    {"name": "Production Assistant", "category": "Team", "units": [2, 10, 11]},
    {"name": "Production Designer", "category": "Team", "units": [2, 10, 11]},
    {"name": "Production Assistant(s)", "category": "Team", "units": [2, 10, 11]},
    {"name": "Set Decorator", "category": "Team", "units": [2, 10, 11]},
    {"name": "Assistant Set Decorator(s)", "category": "Team", "units": [2, 10, 11]},
    {"name": "Property Master", "category": "Team", "units": [2, 10, 11]},
    {"name": "Assistant Property Master", "category": "Team", "units": [2, 10, 11]},
    {"name": "On-Set Props Assistant(s)", "category": "Team", "units": [2, 10, 11]},
    {"name": "Worker(s)", "category": "Team", "units": [2, 10, 11]},
]


def seed_aicp_data(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Unit = apps.get_model("catalog", "Unit")
    Entry = apps.get_model("catalog", "Entry")
    EntryUnit = apps.get_model("catalog", "EntryUnit")

    EntryUnit.objects.all().delete()
    Entry.objects.all().delete()
    Category.objects.all().delete()

    SYMBOL_TO_NAME = {
        "d": "Day",
        "h": "Hour",
        "ea": "Each",
        "flat": "Flat",
        "pers": "Person",
    }
    unit_map = {}
    for unit in Unit.objects.all():
        mapped_name = SYMBOL_TO_NAME.get(unit.symbol)
        if mapped_name:
            unit_map[mapped_name] = unit

    category_cache = {}
    for cat_data in CATEGORIES:
        parent = category_cache.get(cat_data["parent"])
        obj = Category.objects.create(
            name=cat_data["name"],
            level=cat_data["level"],
            code=cat_data["code"],
            tags=cat_data["tags"],
            parent_category=parent,
        )
        category_cache[cat_data["name"]] = obj

    category_by_code = {}
    for cat_data in CATEGORIES:
        if cat_data["code"]:
            category_by_code[cat_data["code"]] = category_cache[cat_data["name"]]

    seen = set()
    for entry_data in ENTRIES:
        dedup_key = (entry_data["cat"], entry_data["name"])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        category = category_by_code[entry_data["cat"]]
        unit = unit_map[entry_data["unit"]]

        entry = Entry.objects.create(
            name=entry_data["name"],
            code=entry_data["code"],
            category=category,
            is_approved=True,
        )
        EntryUnit.objects.create(
            entry=entry,
            unit=unit,
            is_default=True,
        )


def unseed_aicp_data(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Unit = apps.get_model("catalog", "Unit")
    Entry = apps.get_model("catalog", "Entry")
    EntryUnit = apps.get_model("catalog", "EntryUnit")

    EntryUnit.objects.all().delete()
    Entry.objects.all().delete()
    Category.objects.all().delete()

    category_cache = {}
    for cat_data in OLD_CATEGORIES:
        parent = category_cache.get(cat_data["parent"])
        obj = Category.objects.create(
            name=cat_data["name"],
            level=cat_data["level"],
            parent_category=parent,
        )
        category_cache[cat_data["name"]] = obj

    unit_cache = {}
    for unit_data in OLD_UNITS:
        obj = Unit.objects.filter(name=unit_data["name"]).first()
        if obj is None:
            obj = Unit.objects.create(
                name=unit_data["name"],
                symbol=unit_data["symbol"],
                dimension=unit_data["dimension"],
            )
        unit_cache[unit_data["key"]] = obj

    for entry_data in OLD_ENTRIES:
        category = category_cache[entry_data["category"]]
        entry = Entry.objects.create(
            name=entry_data["name"],
            category=category,
            is_approved=True,
        )
        for i, unit_key in enumerate(entry_data["units"]):
            unit = unit_cache[unit_key]
            EntryUnit.objects.create(
                entry=entry,
                unit=unit,
                is_default=(i == 0),
            )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0009_category_tags"),
    ]

    operations = [
        migrations.RunPython(seed_aicp_data, reverse_code=unseed_aicp_data),
    ]
