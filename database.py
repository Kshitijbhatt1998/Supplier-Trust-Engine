from sqlalchemy import create_column, Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy import create_engine
import datetime
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./supplier_trust.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    country = Column(String)
    shipment_count = Column(Integer, default=0)
    importyeti_url = Column(String, unique=True, index=True)
    trust_score = Column(Float, nullable=True)
    last_scraped_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    # Relationships for analysis
    relationships = relationship("Relationship", back_populates="supplier")
    certifications = relationship("Certification", back_populates="supplier")

class Relationship(Base):
    __tablename__ = "relationships"

    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"))
    related_company_name = Column(String)
    relationship_type = Column(String) # e.g., "Customer", "Supplier"
    
    supplier = relationship("Supplier", back_populates="relationships")

class Certification(Base):
    __tablename__ = "certifications"

    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"))
    name = Column(String)
    issued_by = Column(String)
    valid_until = Column(DateTime, nullable=True)
    is_verified = Column(Integer, default=0) # 0 for No, 1 for Yes
    
    supplier = relationship("Supplier", back_populates="certifications")

def init_db():
    Base.metadata.create_all(bind=engine)

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
