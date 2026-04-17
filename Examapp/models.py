from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class UserRole(models.Model):
    ROLE_CHOICES = (
        ('student', 'Student'),
        ('teacher', 'Teacher'),
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    course = models.CharField(max_length=100, blank=True, default='')
    section = models.CharField(max_length=50, blank=True, default='')

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"


class Exam(models.Model):
    teacher = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_exams')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    duration_minutes = models.IntegerField()
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    total_marks = models.IntegerField(default=100)
    passing_marks = models.IntegerField(default=40)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_published = models.BooleanField(default=False)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-created_at']


class StudentExam(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assigned_exams')
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='student_exams')
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_submitted = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(null=True, blank=True)
    obtained_marks = models.IntegerField(null=True, blank=True)
    exam_started_at = models.DateTimeField(null=True, blank=True)
    total_suspicion_warnings = models.IntegerField(default=0)
    tab_switch_count = models.IntegerField(default=0)

    class Meta:
        unique_together = ['student', 'exam']

    def __str__(self):
        return f"{self.student.username} - {self.exam.title}"

    def has_passed(self):
        if self.obtained_marks is None:
            return False
        return self.obtained_marks >= self.exam.passing_marks


class Question(models.Model):
    QUESTION_TYPES = (
        ('mcq', 'Multiple Choice'),
        ('true_false', 'True/False'),
        ('short_answer', 'Short Answer'),
        ('coding', 'Coding (C)'),
    )

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='questions')
    question_text = models.TextField()
    question_type = models.CharField(max_length=20, choices=QUESTION_TYPES)
    marks = models.IntegerField(default=1)
    order = models.IntegerField()

    def __str__(self):
        return f"{self.exam.title} - Q{self.order}"

    class Meta:
        ordering = ['exam', 'order']


class CodingTestCase(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='test_cases')
    stdin = models.TextField(blank=True)
    expected_output = models.TextField()
    marks = models.IntegerField(default=1)
    is_hidden = models.BooleanField(default=False)  # hidden = grading only, student can't see expected
    order = models.IntegerField(default=1)

    class Meta:
        ordering = ['question', 'order']

    def __str__(self):
        return f"TestCase {self.order} — {self.question}"


class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='choices')
    choice_text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)
    order = models.IntegerField()

    def __str__(self):
        return self.choice_text

    class Meta:
        ordering = ['question', 'order']


class Answer(models.Model):
    student_exam = models.ForeignKey(StudentExam, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_choice = models.ForeignKey(Choice, on_delete=models.SET_NULL, null=True, blank=True)
    written_answer = models.TextField(blank=True)
    is_correct = models.BooleanField(null=True, blank=True)
    marks_obtained = models.IntegerField(null=True, blank=True)
    answered_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['student_exam', 'question']

    def __str__(self):
        return f"{self.student_exam} - Q{self.question.order}"


class ProctorSnapshot(models.Model):
    SNAP_TYPES = (('webcam', 'Webcam'), ('screen', 'Screen'))
    student_exam = models.ForeignKey(StudentExam, on_delete=models.CASCADE, related_name='snapshots')
    snapshot_type = models.CharField(max_length=10, choices=SNAP_TYPES, default='webcam')
    image_data = models.TextField()          # base64-encoded JPEG (no data:... prefix)
    captured_at = models.DateTimeField(auto_now_add=True)
    is_flagged = models.BooleanField(default=False)
    flag_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['captured_at']

    def __str__(self):
        return f"Snapshot {self.id} — {self.student_exam.student.username} [{self.snapshot_type}]"


# ── Proxy models for separate admin sections ─────────────────────────────────

class StudentProxy(User):
    class Meta:
        proxy = True
        verbose_name = 'Student'
        verbose_name_plural = 'Students'
        app_label = 'Examapp'


class TeacherProxy(User):
    class Meta:
        proxy = True
        verbose_name = 'Teacher'
        verbose_name_plural = 'Teachers'
        app_label = 'Examapp'


class PasswordResetOTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='otp_codes')
    otp = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    def is_expired(self):
        return (timezone.now() - self.created_at).total_seconds() > 600  # 10 minutes

    def __str__(self):
        return f"{self.user.username} - OTP({self.otp})"
