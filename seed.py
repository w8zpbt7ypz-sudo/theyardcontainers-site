"""Seed demo orders (paid + pending) so the dispatch board has data."""
import datetime, server
server.init_db()
t = datetime.date.today()
demo = [("buy","20","delivered","Demo Customer A","Portage, MI 49002",1,"49002"),("rent","40","delivered","Demo Customer B","Galesburg, MI 49053",1,"49053"),
        ("lease","20","delivered","Demo Contractor C","Richland, MI 49083",2,"49083"),("buy","40","pickup","Demo Customer D","Comstock yard pickup",2,""),
        ("rto","20","delivered","Demo Customer E","Kalamazoo, MI 49048",3,"49048")]
for prod,size,mode,name,addr,days,z in demo:
    oid = server.create_order(dict(product=prod,size=size,mode=mode,name=name,phone="(269) 555-0100",email="demo@example.com",address=addr,zip=z,
                                   date=(t+datetime.timedelta(days=days)).isoformat(),notes="DEMO seed order"))
    server.mark_paid(oid, "demo_seed")
server.create_order(dict(product="lease",size="40",name="Demo Pending F",phone="(269) 555-0100",email="demo@example.com",address="Battle Creek, MI",zip="49017",date=(t+datetime.timedelta(days=4)).isoformat()))
print("seeded")
